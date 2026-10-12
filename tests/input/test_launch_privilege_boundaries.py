#!/usr/bin/env python3
"""Text gates for three privilege boundaries the host used to cross as SYSTEM or hold open forever:
the Playnite plugin deploy runs under the interactive user's token (every filesystem write inside
the impersonated lambda, which has its own catch so RevertToSelf always runs); the per-app output
log is opened in launch_app_commands() under the user token and never by execute(); and an
unauthenticated RTSP socket has an idle timer that every completion handler cancels first, with
the pending-session candidates released at both binding points. --baseline reads HEAD.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    if '--baseline' in sys.argv:
        return subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT, text=True, encoding='utf-8')
    return (ROOT / path).read_text(encoding='utf-8')


def block(source, marker, start_at=0):
    start = source.index(marker, start_at)
    end = source.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (source[end] == '{') - (source[end] == '}')
    return source[start:end + 1]


failures = 0


def check(ok, name):
    global failures
    print(('PASS: ' if ok else 'FAIL: ') + name)
    failures += not ok


playnite = read('src/platform/windows/playnite_integration.cpp')
install = block(playnite, 'static bool do_install_plugin_impl(')
deploy = block(install, 'const auto deploy = [&]()') if 'const auto deploy = [&]()' in install else ''
writes = ['create_directories(', 'copy_file(', 'MoveFileExW(', 'std::filesystem::remove(']
check(bool(deploy) and all(install.count(w) == deploy.count(w) for w in writes),
      'every plugin filesystem write sits inside the deploy lambda')
check('catch (...)' in deploy and 'auto cleanup_guard' in deploy, 'the deploy lambda catches and owns its cleanup guard (RevertToSelf always runs)')
check('platf::impersonate_current_user(deploy_token, deploy)' in install and 'acquire_preferred_user_token_for_playnite()' in install,
      'the deploy lambda runs under the interactive user token when the host is SYSTEM')
check(install.index('launch_playnite(playniteExe)', install.index('const auto deploy')) > install.index('platf::impersonate_current_user(deploy_token, deploy)'),
      'Playnite is relaunched outside the impersonated scope')

process = read('src/process.cpp')
execute = block(process, 'int proc_t::execute(')
launch = block(process, 'int proc_t::launch_app_commands(')
check('_wfsopen(' not in execute and 'fopen(' not in execute, 'execute() no longer opens the output log as SYSTEM before the user-session gate')
check(launch.count('_wfsopen(') == 1 and launch.index('_wfsopen(') < launch.index('auto fg = util::fail_guard'),
      'launch_app_commands() opens the output log once at its top')
open_log = block(launch, 'const auto open_log = [&]()')
check('_wfsopen(' in open_log and 'platf::impersonate_current_user(log_token, open_log)' in launch and 'retrieve_users_token(false)' in launch,
      'the output log is opened under the user token when running as SYSTEM')
check('if (!_pipe && !_app.output.empty()' in launch, 'a re-entered launch keeps the open handle')

rtsp = read('src/rtsp.cpp')
socket_class = block(rtsp, 'class socket_t: public std::enable_shared_from_this<socket_t>')
check('boost::asio::steady_timer idle_timer;' in socket_class and 'void arm_idle_timer()' in socket_class, 'socket_t carries the idle timer')
check('arm_idle_timer();' in block(socket_class, 'void read_unbound()'), 'an unbound read arms the idle timer')
for handler in ('handle_read_unbound_prefix(', 'handle_read_unbound_encrypted_header_after_prefix(', 'handle_read_unbound_encrypted_message(',
                'handle_read_encrypted_header(', 'handle_read_plaintext('):
    body = block(socket_class, 'static void ' + handler)
    first = body[body.index('{') + 1:].lstrip()
    check(first.startswith('socket->idle_timer.cancel();'), handler + ' cancels the idle timer first')
binds = [m.end() for m in re.finditer(r'socket->session = std::move\(reserved\);', socket_class)]
check(len(binds) == 2 and all('encrypted_candidates.clear();' in socket_class[b:b + 260] and 'plaintext_candidate.reset();' in socket_class[b:b + 260] for b in binds),
      'both binding points release the pending-session candidates')
check(socket_class.count('socket->first_message_done = true;') == 3, 'every dispatch marks the first message done')
check('socket.idle_timer.cancel();' in block(rtsp, 'void shutdown_socket(socket_t &socket)'), 'shutdown cancels the idle timer')
sys.exit(1 if failures else 0)
