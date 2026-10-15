import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test, type TestContext } from 'node:test';

// Execute the full production client. Its two imports contain only interfaces;
// HTTP promises, browser objects and clocks are replaced at their boundaries.
const source = readFileSync(
  new URL('../../web-legacy/utils/webrtc/client.ts', import.meta.url),
  'utf8',
);
const code = stripTypeScriptTypes(source.replace(/^import .* from '@\/[^']+';\r?\n/gm, ''));
const { WebRtcClient } = await import(`data:text/javascript,${encodeURIComponent(code)}`);

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const config = {
  encoding: 'h264',
  hdr: false,
  bitrateKbps: 10000,
  fps: 60,
  width: 1920,
  height: 1080,
};
const answer = { type: 'answer', sdp: 'answer' };
const candidate = { candidate: 'candidate:1 1 udp 1 192.0.2.1 1234 typ host', sdpMid: '0' };

function setup(t: TestContext) {
  const peers: FakePeer[] = [];
  class FakeStream {
    tracks: unknown[] = [];
    getTracks() {
      return this.tracks;
    }
    addTrack(track: unknown) {
      this.tracks.push(track);
    }
    removeTrack(track: unknown) {
      this.tracks = this.tracks.filter((v) => v !== track);
    }
  }
  class FakePeer {
    connectionState = 'new';
    iceConnectionState = 'new';
    remoteDescription: unknown = null;
    descriptions: unknown[] = [];
    candidates: unknown[] = [];
    closed = false;
    channel = {
      readyState: 'open',
      sent: [] as unknown[],
      close() {
        this.readyState = 'closed';
      },
      send(value: unknown) {
        this.sent.push(value);
      },
    };
    constructor() {
      peers.push(this);
    }
    addTransceiver() {
      return { setCodecPreferences() {} };
    }
    createDataChannel() {
      return this.channel;
    }
    async createOffer() {
      return {
        type: 'offer',
        sdp: 'm=video 9 UDP/TLS/RTP/SAVPF 96 97\r\na=rtpmap:96 H264/90000\r\na=rtpmap:97 H265/90000\r\n',
      };
    }
    async setLocalDescription() {}
    async setRemoteDescription(value: unknown) {
      this.descriptions.push(value);
      this.remoteDescription = value;
    }
    async addIceCandidate(value: unknown) {
      this.candidates.push(value);
    }
    async getStats() {
      await wait(`stats:${peers.indexOf(this) + 1}`);
      return new Map();
    }
    getReceivers() {
      return [];
    }
    close() {
      this.closed = true;
    }
  }
  let timerId = 0;
  const timers = new Map<number, () => void>();
  const globals = {
    window: {
      setTimeout(callback: () => void) {
        timers.set(++timerId, callback);
        return timerId;
      },
      clearTimeout(id: number) {
        timers.delete(id);
      },
    },
    MediaStream: FakeStream,
    RTCPeerConnection: FakePeer,
  };
  const restore: Array<() => void> = [];
  for (const [name, value] of Object.entries(globals)) {
    const old = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, value });
    restore.push(() => {
      if (old) Object.defineProperty(globalThis, name, old);
      else Reflect.deleteProperty(globalThis, name);
    });
  }
  type Gate = ReturnType<typeof deferred<void>>;
  const pending: Record<string, { entered: Gate; gate: Gate }> = {};
  const created: string[] = [];
  const ended: string[] = [];
  const errors: unknown[] = [];
  const stats: unknown[] = [];
  const subscribers = new Map<string, (value: unknown) => void>();
  async function wait(key: string) {
    const hold = pending[key];
    if (hold) {
      delete pending[key];
      hold.entered.resolve();
      await hold.gate.promise;
    }
  }
  const api = {
    async createSession() {
      const sessionId = `session-${created.length + 1}`;
      created.push(sessionId);
      await wait(`create:${sessionId}`);
      return { sessionId, iceServers: [] };
    },
    async sendOffer(id: string) {
      await wait(`offer:${id}`);
      return { ...answer, sdp: id };
    },
    async endSession(id: string) {
      ended.push(id);
      await wait(`delete:${id}`);
    },
    async sendIceCandidates() {},
    subscribeRemoteCandidates(id: string, callback: (value: unknown) => void) {
      subscribers.set(id, callback);
      return () => {
        subscribers.delete(id);
      };
    },
  };
  const client = new WebRtcClient(api);
  const gates: Gate[] = [];
  t.after(async () => {
    for (const gate of gates) gate.resolve();
    await client.disconnect();
    for (const reset of restore) reset();
  });
  return {
    client,
    peers,
    created,
    ended,
    timers,
    errors,
    stats,
    subscribers,
    connect(value = config) {
      return client.connect(value, {
        onError(error: unknown) {
          errors.push(error);
        },
        onStats(value: unknown) {
          stats.push(value);
        },
      });
    },
    hold(key: string) {
      const hold = { entered: deferred(), gate: deferred() };
      pending[key] = hold;
      gates.push(hold.gate);
      return hold;
    },
  };
}

function canceled(result: unknown) {
  assert.equal((result as Error)?.name, 'WebRtcConnectionCanceledError');
}

test('legacy: disconnect during create deletes the late session without creating a peer', async (t) => {
  const h = setup(t);
  const delayed = h.hold('create:session-1');
  const connecting = h.connect().catch((error: unknown) => error);
  await delayed.entered.promise;
  await h.client.disconnect();
  delayed.gate.resolve();
  canceled(await connecting);
  assert.deepEqual(h.ended, ['session-1']);
  assert.equal(h.peers.length, 0);
  assert.equal(h.timers.size, 0);
  assert.deepEqual(h.errors, []);
});

test('legacy: an old create response cannot replace the new connection', async (t) => {
  const h = setup(t);
  const delayed = h.hold('create:session-1');
  const connecting = h.connect().catch((error: unknown) => error);
  await delayed.entered.promise;
  assert.equal(await h.connect(), 'session-2');
  const current = h.client.peerConnection;
  delayed.gate.resolve();
  canceled(await connecting);
  assert.equal(h.client.peerConnection, current);
  assert.equal(current.closed, false);
  assert.deepEqual(h.ended, ['session-1']);
  assert.deepEqual(h.errors, []);
});

test('legacy: an old answer cannot be applied to the replacement peer', async (t) => {
  const h = setup(t);
  const delayed = h.hold('offer:session-1');
  const connecting = h.connect().catch((error: unknown) => error);
  await delayed.entered.promise;
  await h.connect();
  const current = h.client.peerConnection;
  delayed.gate.resolve();
  canceled(await connecting);
  assert.equal(h.client.peerConnection, current);
  assert.deepEqual(current.descriptions, [{ ...answer, sdp: 'session-2' }]);
  assert.equal(current.closed, false);
  assert.deepEqual(h.errors, []);
});

test('legacy: a stale codec error cannot start fallback or tear down a replacement', async (t) => {
  const h = setup(t);
  const delayed = h.hold('offer:session-1');
  const connecting = h.connect({ ...config, encoding: 'hevc' }).catch((error: unknown) => error);
  await delayed.entered.promise;
  await h.connect();
  const current = h.client.peerConnection;
  delayed.gate.reject(new Error('Failed to process offer'));
  canceled(await connecting);
  assert.equal(h.client.peerConnection, current);
  assert.equal(current.closed, false);
  assert.deepEqual(h.created, ['session-1', 'session-2']);
  assert.deepEqual(h.errors, []);
});

test('legacy: finishing an old DELETE leaves replacement resources intact', async (t) => {
  const h = setup(t);
  await h.connect();
  const delayed = h.hold('delete:session-1');
  const closing = h.client.disconnect();
  await delayed.entered.promise;
  await h.connect();
  const current = h.client.peerConnection;
  delayed.gate.resolve();
  await closing;
  assert.equal(h.client.peerConnection, current);
  assert.equal(h.client.sendInput('new input'), true);
  assert.deepEqual(current.channel.sent, ['new input']);
});

test('legacy: callbacks retained from a retired connection cannot target the next one', async (t) => {
  const h = setup(t);
  await h.connect();
  const oldCandidate = h.subscribers.get('session-1')!;
  const oldState = h.client.peerConnection.onconnectionstatechange;
  await h.connect();
  const current = h.client.peerConnection;
  current.connectionState = 'failed';
  oldCandidate(candidate);
  oldState();
  await Promise.resolve();
  assert.deepEqual(current.candidates, []);
  assert.equal(current.closed, false);
  assert.deepEqual(h.ended, ['session-1']);
});

test('legacy: an active codec failure can still fall back to H.264', async (t) => {
  const h = setup(t);
  const delayed = h.hold('offer:session-1');
  const connecting = h.connect({ ...config, encoding: 'hevc' });
  await delayed.entered.promise;
  delayed.gate.reject(new Error('Failed to process offer'));
  assert.equal(await connecting, 'session-2');
  assert.deepEqual(h.created, ['session-1', 'session-2']);
  assert.deepEqual(h.ended, ['session-1']);
  assert.deepEqual(h.errors, []);
});

test('legacy: an in-flight stats response cannot publish or restart retired polling', async (t) => {
  const h = setup(t);
  const delayed = h.hold('stats:1');
  await h.connect();
  await delayed.entered.promise;
  await h.connect();
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(h.stats.length, 1);
  const timers = [...h.timers.keys()];
  delayed.gate.resolve();
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(h.stats.length, 1);
  assert.deepEqual([...h.timers.keys()], timers);
});
