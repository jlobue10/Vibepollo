import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test, type TestContext } from 'node:test';

// Load the complete production service with only its HTTP boundary replaced.
// No browser, npm packages, network requests, or wall-clock timers are needed.
const apiUrl = `data:text/javascript,${encodeURIComponent(`
  let api;
  export const setApi = (value) => { api = value; };
  export const apiGet = (...args) => api.get(...args);
  export const apiPost = (...args) => api.post(...args);
  export const apiDelete = (...args) => api.delete(...args);
  export class ApiError extends Error {}
`)}`;
const { setApi } = await import(apiUrl);
const source = readFileSync(new URL('../services/webrtc.ts', import.meta.url), 'utf8');
const code = stripTypeScriptTypes(source.replace("'@/api/client'", JSON.stringify(apiUrl)))
  + '\n//# sourceURL=production-webrtc-service.js';
const { BrowserWebRtcSession } = await import(`data:text/javascript,${encodeURIComponent(code)}`);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const candidate = { candidate: 'candidate:1 1 udp 1 192.0.2.1 1234 typ host', index: 1, sdpMid: '0' };
const config = { appId: 1, encoding: 'h264', fps: 60, bitrateKbps: 10000, width: 1920, height: 1080 };

function setup(t: TestContext) {
  const peers: FakePeer[] = [];
  class FakePeer {
    remoteDescription: unknown = null;
    candidates: unknown[] = [];
    constructor() { peers.push(this); }
    addTransceiver() { return { setCodecPreferences() {} }; }
    createDataChannel() { return { close() {} }; }
    async createOffer() {
      return { type: 'offer', sdp: 'm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=rtpmap:96 H264/90000\r\n' };
    }
    async setLocalDescription() {}
    async setRemoteDescription(value: unknown) { this.remoteDescription = value; }
    async addIceCandidate(value: unknown) { this.candidates.push(value); }
    async getStats() { return new Map(); }
    close() {}
  }
  let timerId = 0;
  const timers = new Map<number, () => void>();
  const schedule = (callback: () => void) => { timers.set(++timerId, callback); return timerId; };
  const globals = {
    window: {
      setTimeout: schedule, clearTimeout: (id: number) => timers.delete(id),
      setInterval: schedule, clearInterval: (id: number) => timers.delete(id),
    },
    RTCPeerConnection: FakePeer,
    // Exercise the HTTP polling fallback used when SSE is unavailable.
    EventSource: class { constructor() { throw new Error('SSE unavailable'); } },
  };
  const restoreGlobals: Array<() => void> = [];
  for (const [name, value] of Object.entries(globals)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, value });
    restoreGlobals.push(() => {
      if (previous) Object.defineProperty(globalThis, name, previous);
      else Reflect.deleteProperty(globalThis, name);
    });
  }
  const delayed = deferred<unknown>();
  const urls: string[] = [];
  let created = 0;
  let firstPoll = true;
  setApi({
    async post(url: string) {
      if (url === '/api/webrtc/sessions') return { session: { id: `session-${++created}` } };
      assert.ok(url.endsWith('/offer'));
      return { answer_ready: true, type: 'answer', sdp: 'answer' };
    },
    async delete() {},
    get(url: string) {
      urls.push(url);
      if (firstPoll) { firstPoll = false; return delayed.promise; }
      const since = Number(new URL(url, 'https://test.invalid').searchParams.get('since'));
      return Promise.resolve({ candidates: since < 1 ? [candidate] : [], next_since: Math.max(since, 1) });
    },
  });
  const session = new BrowserWebRtcSession();
  t.after(async () => {
    await session.disconnect();
    for (const restore of restoreGlobals) restore();
  });
  return { session, peers, delayed, urls, timers };
}

test('an old ICE poll cannot skip the replacement session\'s candidates', async (t) => {
  const { session, peers, delayed, urls } = setup(t);
  await session.connect(config);
  const oldPoll = session.pollRemoteCandidates(session.generation);
  await session.disconnect();
  await session.connect(config);
  delayed.resolve({ candidates: [{ ...candidate, index: 42 }], next_since: 42 });
  await oldPoll;
  await session.pollRemoteCandidates(session.generation);
  assert.equal(urls.at(-1), '/api/webrtc/sessions/session-2/ice?since=0');
  assert.equal(peers[0].candidates.length, 0, 'the retired peer receives no late candidate');
  assert.equal(peers[1].candidates.length, 1, 'the replacement receives its first ICE candidate');
  assert.equal(session.remoteCandidateIndex, 1);
});

test('a poll completing after disconnect leaves the retired cursor and timers clear', async (t) => {
  const { session, delayed, timers } = setup(t);
  await session.connect(config);
  const oldPoll = session.pollRemoteCandidates(session.generation);
  await session.disconnect();
  delayed.resolve({ next_since: 42 });
  await oldPoll;
  assert.equal(session.remoteCandidateIndex, 0);
  assert.equal(timers.size, 0);
});

test('a current ICE poll delivers candidates and advances the next request', async (t) => {
  const { session, peers, delayed, urls } = setup(t);
  await session.connect(config);
  const poll = session.pollRemoteCandidates(session.generation);
  delayed.resolve({ candidates: [candidate], next_since: 5 });
  await poll;
  await session.pollRemoteCandidates(session.generation);
  assert.equal(peers[0].candidates.length, 1);
  assert.equal(urls.at(-1), '/api/webrtc/sessions/session-1/ice?since=5');
  assert.equal(session.remoteCandidateIndex, 5);
});
