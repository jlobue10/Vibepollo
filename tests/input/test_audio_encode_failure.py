#!/usr/bin/env python3
"""Execute the production audio encode thread with a failing Opus encoder.

The audio packet queue is shared by every session's broadcast; an encode failure used to stop
that queue and thereby end all sessions. It must end only the failing session (its shutdown
event) and leave the shared queue running. Uses the real safe::queue_t / mail; no audio device.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = 'src/audio.cpp'
source = (subprocess.check_output(['git', 'show', 'HEAD:' + PATH], cwd=ROOT, text=True)
          if '--baseline' in sys.argv else (ROOT / PATH).read_text())

def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start)
    depth = 1
    while depth:
        end += 1
        depth += (text[end] == '{') - (text[end] == '}')
    return text[start:end + 1]

prefix = r"""
#include <cstdint>
#include <iostream>
#include <memory>
#include <string_view>
#include <vector>
#include "src/thread_safe.h"
using namespace std::literals;
struct Log { template<class T> Log &operator<<(const T &) { return *this; } };
#define BOOST_LOG(level) Log{}
typedef struct OpusMSEncoder OpusMSEncoder;
typedef int opus_int32;
#define OPUS_APPLICATION_RESTRICTED_LOWDELAY 2051
#define OPUS_SET_BITRATE(x) 4002, (opus_int32)(x)
#define OPUS_SET_VBR(x) 4006, (opus_int32)(x)
static int encodeResult = -1;
OpusMSEncoder *opus_multistream_encoder_create(int, int, int, int, const unsigned char *, int, int *) { static int token; return (OpusMSEncoder *) &token; }
void opus_multistream_encoder_destroy(OpusMSEncoder *) {}
int opus_multistream_encoder_ctl(OpusMSEncoder *, int, ...) { return 0; }
int opus_multistream_encode_float(OpusMSEncoder *, const float *, int, unsigned char *, opus_int32) { return encodeResult; }
const char *opus_strerror(int) { return "bad arg"; }
namespace util {
  template<class T, void (*D)(T *)> struct safe_ptr { T *p; explicit safe_ptr(T *q): p {q} {} T *get() { return p; } ~safe_ptr() { D(p); } };
  struct buffer_t { std::vector<std::uint8_t> v; explicit buffer_t(std::size_t n): v(n) {} std::uint8_t *begin() { return v.data(); } std::uint8_t *end() { return v.data() + v.size(); } std::size_t size() const { return v.size(); } void fake_resize(std::size_t n) { v.resize(n); } };
}
namespace platf { enum class thread_priority_e { high }; void set_thread_name(std::string_view) {} void adjust_thread_priority(thread_priority_e) {} }
namespace webrtc_stream { bool has_active_sessions() { return false; } template<class... A> void submit_audio_frame(A &&...) {} }
namespace mail {
  constexpr auto audio_packets = "audio_packets"sv;
  constexpr auto shutdown = "shutdown"sv;
  safe::mail_t man = std::make_shared<safe::mail_raw_t>();
}
namespace stream { using packet_channel_t = std::shared_ptr<int>; }
namespace audio {
  using opus_t = util::safe_ptr<OpusMSEncoder, opus_multistream_encoder_destroy>;
  using buffer_t = util::buffer_t;
  using packet_t = std::pair<stream::packet_channel_t, buffer_t>;
  using sample_queue_t = std::shared_ptr<safe::queue_t<std::vector<float>>>;
  struct stream_params_t {};
  struct opus_stream_config_t { int sampleRate = 48000, channelCount = 2, streams = 1, coupledStreams = 1, bitrate = 96000; const unsigned char *mapping = nullptr; };
  opus_stream_config_t stream_configs[1];
  int map_stream(int, bool) { return 0; }
  void apply_surround_params(opus_stream_config_t &, const stream_params_t &) {}
  struct config_t {
    enum flags_e { HIGH_QUALITY, HOST_AUDIO, CONTINUOUS_AUDIO, CUSTOM_SURROUND_PARAMS, MAX_FLAGS };
    int packetDuration = 5, channels = 2; bool flags[MAX_FLAGS] {}; stream_params_t customStreamParams;
  };
"""
suffix = r"""
}
int main() {
  using namespace audio;
  int failures = 0, checks = 0;
  auto check = [&](bool ok, const char *name) { ++checks; failures += !ok; std::cout << (ok ? "PASS " : "FAIL ") << name << '\n'; };
  auto packets = mail::man->queue<packet_t>(mail::audio_packets);
  auto session_mail = std::make_shared<safe::mail_raw_t>();
  auto other_session_mail = std::make_shared<safe::mail_raw_t>();
  auto shutdown_event = session_mail->event<bool>(mail::shutdown);
  auto samples = std::make_shared<safe::queue_t<std::vector<float>>>(30);
  samples->raise(std::vector<float>(480, 0.0f));
  config_t config;
  encodeResult = -1;
  encodeThread(samples, config, std::make_shared<int>(1), session_mail);
  check(packets->running(), "an encode failure leaves the shared audio packet queue running");
  check(shutdown_event->peek(), "an encode failure ends the failing session");
  check(!other_session_mail->event<bool>(mail::shutdown)->peek(), "other sessions are untouched");
  encodeResult = 120;
  auto samples2 = std::make_shared<safe::queue_t<std::vector<float>>>(30);
  samples2->raise(std::vector<float>(480, 0.0f));
  samples2->stop();
  encodeThread(samples2, config, std::make_shared<int>(2), other_session_mail);
  auto produced = packets->pop();
  check(produced && produced->second.size() == 120 && !other_session_mail->event<bool>(mail::shutdown)->peek(),
        "a successful encode publishes its packet without ending the session");
  std::cout << checks << " checks; " << failures << " failures\n";
  return failures ? 1 : 0;
}
"""
code = prefix + block(source, 'void encodeThread(') + suffix
with tempfile.TemporaryDirectory(prefix='audio-encode-') as directory:
    work = Path(directory)
    (work / 'test.cpp').write_text(code)
    subprocess.run(['g++', '-std=c++20', '-g', '-pthread', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I' + str(ROOT), '-I' + os.environ.get('AUDIT_JSON_INCLUDE', '/usr/include'),
                    str(work / 'test.cpp'), '-o', str(work / 'test')], check=True)
    raise SystemExit(subprocess.run([str(work / 'test')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')).returncode)
