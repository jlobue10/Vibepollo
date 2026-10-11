/**
 * @file src/platform/windows/playnite_sync.cpp
 * @brief Runtime adapters for the portable Playnite sync policy.
 */

#include "playnite_sync.h"

#include "src/logging.h"
#include "src/platform/windows/playnite_integration.h"
#include "src/uuid.h"

#include <cctype>
#include <sstream>
#include <string_view>

#include <windows.h>

namespace platf::playnite::sync {
  namespace {
    bool ensure_runtime_app_uuid(nlohmann::json &app) {
      try {
        if (app.contains("playnite-id") && app["playnite-id"].is_string() && !app["playnite-id"].get<std::string>().empty()) {
          return false;
        }
        const bool missing_uuid = !app.contains("uuid") || app["uuid"].is_null() || (app["uuid"].is_string() && app["uuid"].get<std::string>().empty());
        if (missing_uuid) {
          app["uuid"] = uuid_util::uuid_t::generate().string();
          return true;
        }
      } catch (...) {
        try {
          app["uuid"] = uuid_util::uuid_t::generate().string();
          return true;
        } catch (...) {}
      }
      return false;
    }
  }  // namespace

  std::string image_source_signature(const std::filesystem::path &src) {
    std::error_code size_error;
    std::error_code time_error;
    const auto size = std::filesystem::file_size(src, size_error);
    const auto modified = std::filesystem::last_write_time(src, time_error);
    if (size_error || time_error) return {};
    std::ostringstream signature;
    signature << policy::normalize_path_for_match(src.string()) << '|' << size << '|' << modified.time_since_epoch().count();
    return signature.str();
  }

  bool is_safe_local_media_path(const std::string &path) {
    // SYSTEM opens these on a peer's say-so (WIC decode, LoadLibraryEx as a data file). Only a
    // plain drive-rooted local path: a UNC name would force SYSTEM network authentication and
    // a device/pipe namespace path would hand a pipe squatter an impersonation client.
    if (path.size() < 4 || !std::isalpha(static_cast<unsigned char>(path[0])) || path[1] != ':' ||
        (path[2] != '\\' && path[2] != '/')) {
      return false;
    }
    if (path.find('\0') != std::string::npos || path.find(':', 2) != std::string::npos) {
      return false;  // embedded NUL, alternate data stream or a nested drive spec
    }
    const std::string_view view(path);
    for (std::size_t i = 3; i <= view.size();) {
      std::size_t end = view.find_first_of("\\/", i);
      if (end == std::string_view::npos) end = view.size();
      const auto segment = view.substr(i, end - i);
      if (segment.empty() || segment == "." || segment == "..") {
        return false;  // "C:\\a\\\\b", "C:\\a\\..\\b", "C:\\a\\" (a file is expected)
      }
      i = end + 1;
    }
    return true;
  }

  namespace {
    // Opens one path component without following a reparse point on it. True when it is a
    // plain file or directory on a disk volume; `directory` reports which.
    bool plain_component(const std::wstring &component_path, bool &directory) {
      HANDLE handle = CreateFileW(component_path.c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                                  FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr);
      if (handle == INVALID_HANDLE_VALUE) return false;
      BY_HANDLE_FILE_INFORMATION info {};
      const bool plain = GetFileInformationByHandle(handle, &info) && (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 && GetFileType(handle) == FILE_TYPE_DISK;
      directory = plain && (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
      CloseHandle(handle);
      return plain;
    }
  }  // namespace

  std::optional<std::wstring> resolve_local_media_path(const std::string &path) {
    if (!is_safe_local_media_path(path)) return std::nullopt;
    std::wstring wide;
    try {
      wide = std::filesystem::path(path).wstring();
    } catch (...) {
      return std::nullopt;
    }
    // Every ancestor is opened as itself (FILE_FLAG_OPEN_REPARSE_POINT) and must be a plain
    // directory before the next component is touched, so a junction planted in a parent can
    // never be followed, not even by this check, to a share (forced SYSTEM authentication)
    // or a device namespace. SECURITY_IDENTIFICATION: a name that still reaches a pipe
    // server grants no impersonation token. The leaf may be a file or a directory.
    // The sinks re-open the resolved path by name afterwards; a swap in that window remains
    // (a handle-based decoder path is the complete fix).
    for (std::size_t i = 3; i < wide.size(); ++i) {
      if (wide[i] != L'\\' && wide[i] != L'/') continue;
      bool directory = false;
      if (!plain_component(wide.substr(0, i), directory) || !directory) return std::nullopt;
    }
    HANDLE handle = CreateFileW(wide.c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr);
    if (handle == INVALID_HANDLE_VALUE) return std::nullopt;
    std::optional<std::wstring> resolved;
    BY_HANDLE_FILE_INFORMATION info {};
    if (GetFileInformationByHandle(handle, &info) && (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 && GetFileType(handle) == FILE_TYPE_DISK) {
      std::wstring buffer(32768, L'\0');
      const DWORD length = GetFinalPathNameByHandleW(handle, buffer.data(), static_cast<DWORD>(buffer.size()), FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
      if (length != 0 && length < buffer.size()) {
        buffer.resize(length);
        // "\\?\C:\..." is a local volume; "\\?\UNC\..." and "\\?\Volume{...}" are not what the peer may name.
        if (buffer.rfind(L"\\\\?\\", 0) == 0) {
          buffer.erase(0, 4);
          try {
            if (is_safe_local_media_path(std::filesystem::path(buffer).string())) resolved = buffer;
          } catch (...) {}
        }
      }
    }
    CloseHandle(handle);
    return resolved;
  }

  bool convert_playnite_image_to_png(const std::string &src_path, const std::filesystem::path &dst) {
    if (src_path.empty()) return false;
    const auto resolved = resolve_local_media_path(src_path);
    if (!resolved) {
      BOOST_LOG(warning) << "Playnite sync: refusing to open image path '" << src_path << "'";
      return false;
    }
    const auto src = std::filesystem::path(*resolved);
    file_handler::make_directory(dst.parent_path().string());
    const auto signature = image_source_signature(src);
    const std::string sidecar = dst.string() + ".src";
    std::error_code exists_error;
    if (!policy::should_reconvert_playnite_image(std::filesystem::exists(dst, exists_error), file_handler::read_file(sidecar.c_str()), signature)) return true;
    if (!platf::img::convert_to_png_96dpi(src.wstring(), dst.wstring())) return false;
    if (!signature.empty()) file_handler::write_file(sidecar.c_str(), signature);
    return true;
  }

  void apply_game_metadata_to_app(const Game &game, nlohmann::json &app, const std::filesystem::path &covers_root) {
    try {
      if (!game.box_art_path.empty()) {
        const auto destination = covers_root / ("playnite_" + game.id + ".png");
        if (convert_playnite_image_to_png(game.box_art_path, destination)) policy::apply_box_art_path(app, destination.generic_string());
      }
    } catch (...) {}
    try {
      const auto destination = covers_root / ("playnite_icon_" + game.id + ".png");
      std::string install_dir = !game.install_dir.empty() ? game.install_dir : game.working_dir;
      if (install_dir.empty()) {
        std::string cached;
        if (platf::playnite::get_cached_install_dir(game.id, cached)) install_dir = cached;
      }
      // Every path here came from the Playnite peer; the icon resolver opens them as SYSTEM, so
      // it only ever sees the resolved paths (handle-checked, no reparse points, drive-rooted).
      while (!install_dir.empty() && (install_dir.back() == '\\' || install_dir.back() == '/')) install_dir.pop_back();
      const std::wstring icon_path = resolve_local_media_path(game.icon_path).value_or(L"");
      const std::wstring exe_path = resolve_local_media_path(game.exe).value_or(L"");
      const std::wstring install_path = resolve_local_media_path(install_dir).value_or(L"");
      platf::img::IconResolutionInfo diagnostics;
      if (platf::img::resolve_best_app_icon_png(icon_path, exe_path, install_path, destination.wstring(), &diagnostics)) {
        policy::apply_icon_path(app, destination.generic_string());
        BOOST_LOG(debug) << "Playnite sync icon: name='" << game.name << "' installDir='" << install_dir << "' exeIcon=" << diagnostics.exe_size << " playniteIcon=" << diagnostics.icon_size << " -> width=" << platf::img::image_pixel_width(destination.wstring());
      } else {
        policy::apply_icon_path(app, {});
      }
    } catch (...) {}
  }

  void apply_game_metadata_to_app(const Game &game, nlohmann::json &app) {
    apply_game_metadata_to_app(game, app, platf::appdata() / "covers");
  }

  void write_and_refresh_apps(nlohmann::json &root, const std::string &apps_path) {
    file_handler::write_file(apps_path.c_str(), root.dump(4));
    confighttp::refresh_client_apps_cache(root, false);
  }

  void autosync_reconcile(nlohmann::json &root, const std::vector<Game> &all_games, int recent_count, int recent_age_days, int delete_after_days, bool require_replacement, bool sync_all_installed, const std::vector<std::string> &categories, const std::vector<std::string> &include_plugins, const std::vector<std::string> &exclude_categories, const std::vector<std::string> &exclude_ids, const std::vector<std::string> &exclude_plugins, bool remove_uninstalled, bool &changed, std::size_t &matched_out, bool manage_membership) {
    bool runtime_changed = false;
    if (root.contains("apps") && root["apps"].is_array()) {
      for (auto &app : root["apps"]) runtime_changed = ensure_runtime_app_uuid(app) || runtime_changed;
    }
    policy::autosync_reconcile(root, all_games, recent_count, recent_age_days, delete_after_days, require_replacement, sync_all_installed, categories, include_plugins, exclude_categories, exclude_ids, exclude_plugins, remove_uninstalled, changed, matched_out, manage_membership, static_cast<policy::MetadataUpdater>(&apply_game_metadata_to_app));
    changed = changed || runtime_changed;
  }
}  // namespace platf::playnite::sync
