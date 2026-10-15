import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test, type TestContext } from 'node:test';
import { runInNewContext } from 'node:vm';

// Run the complete view script and WebRTC service. Only Vue's ref/lifecycle
// primitives, HTTP, and browser devices/timers are replaced; no npm install or
// actual stream is needed to exercise delayed signaling responses.
const apiUrl = `data:text/javascript,${encodeURIComponent(`
  let api;
  export const setApi = (value) => { api = value; };
  export const apiGet = (...args) => api.get(...args);
  export const apiPost = (...args) => api.post(...args);
  export const apiDelete = (...args) => api.delete(...args);
  export class ApiError extends Error {}
`)}`;
const { setApi, apiGet, apiPost } = await import(apiUrl);
const serviceSource = readFileSync(new URL('../services/webrtc.ts', import.meta.url), 'utf8');
const serviceCode = stripTypeScriptTypes(
  serviceSource.replace("'@/api/client'", JSON.stringify(apiUrl)),
) + '\n//# sourceURL=production-webrtc-service.js';
const service = await import(`data:text/javascript,${encodeURIComponent(serviceCode)}`);
const viewSource = readFileSync(new URL('../views/BrowserStreamView.vue', import.meta.url), 'utf8');
const viewScript = viewSource.split('<script setup lang="ts">')[1].split('</script>')[0];
const viewCode = stripTypeScriptTypes(viewScript).replace(/^import\s[\s\S]*?from\s+'[^']+';\s*/gm, '')
  + `\nObject.assign(globalThis, { connect, disconnect, browserSession,
    loading, hostCapabilities, browserCapabilities, connectionState, inputReady,
    isConnecting, startDisabled, streamError, nativeFullscreen, videoEl, audioEl,
    confirmTerminate, startAfterTerminate, sessionActionPending, sessionActionError });`;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const answer = { answer_ready: true, type: 'answer', sdp: 'answer' };

function setup(t: TestContext, blocked: 'create' | 'offer' | 'none' = 'create') {
  const peers: FakePeer[] = [];
  class FakePeer {
    remoteDescription: unknown = null;
    connectionState = 'new';
    onconnectionstatechange = () => {};
    channel = { readyState: 'connecting', onopen() {}, close() {}, send() {} };
    constructor() { peers.push(this); }
    addTransceiver() { return { setCodecPreferences() {} }; }
    createDataChannel() { return this.channel; }
    async createOffer() {
      return { type: 'offer', sdp: 'm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=rtpmap:96 H264/90000\r\n' };
    }
    async setLocalDescription() {}
    async setRemoteDescription(value: unknown) { this.remoteDescription = value; }
    async addIceCandidate() {}
    async getStats() { return new Map(); }
    close() { this.connectionState = 'closed'; }
    connected() {
      this.connectionState = 'connected';
      this.onconnectionstatechange();
      this.channel.readyState = 'open';
      this.channel.onopen();
    }
  }
  let timerId = 0;
  let fullscreenExits = 0;
  const timers = new Map<number, { callback: () => void; ms: number }>();
  const schedule = (callback: () => void, ms: number) => {
    timers.set(++timerId, { callback, ms });
    return timerId;
  };
  const document = {
    fullscreenElement: null as unknown,
    visibilityState: 'visible',
    async exitFullscreen() { ++fullscreenExits; document.fullscreenElement = null; },
    removeEventListener() {},
  };
  const window = {
    setTimeout: schedule, clearTimeout: (id: number) => timers.delete(id),
    setInterval: schedule, clearInterval: (id: number) => timers.delete(id),
    removeEventListener() {},
  };
  const globals = {
    window, RTCPeerConnection: FakePeer,
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
  const response = deferred<unknown>();
  const reached = deferred<void>();
  const nextAnswer = deferred<unknown>();
  const nextOfferReached = deferred<void>();
  const deleteResponse = deferred<void>();
  const deleteReached = deferred<void>();
  const closeResponse = deferred<unknown>();
  const closeReached = deferred<void>();
  const statusResponse = deferred<unknown>();
  const statusReached = deferred<void>();
  const deletes: string[] = [];
  let created = 0;
  let holdNextAnswer = false;
  let holdDelete = false;
  let holdStatus = false;
  setApi({
    async post(url: string) {
      if (url === '/api/apps/close') { closeReached.resolve(); return closeResponse.promise; }
      if (url === '/api/webrtc/sessions') {
        const id = `session-${++created}`;
        if (created === 1 && blocked === 'create') { reached.resolve(); return response.promise; }
        return { session: { id } };
      }
      assert.ok(url.endsWith('/offer'));
      if (url.includes('/session-1/') && blocked === 'offer') {
        reached.resolve(); return response.promise;
      }
      if (holdNextAnswer) { nextOfferReached.resolve(); return nextAnswer.promise; }
      return answer;
    },
    async delete(url: string) {
      deletes.push(url);
      if (holdDelete) { holdDelete = false; deleteReached.resolve(); await deleteResponse.promise; }
    },
    async get(url: string) {
      if (url === '/api/session/status' && holdStatus) {
        statusReached.resolve();
        return statusResponse.promise;
      }
      return { status: true, candidates: [], next_since: 0 };
    },
  });
  const unmount: Array<() => void> = [];
  const view = {
    ...service, apiGet, apiPost, window, document, navigator: {}, Error,
    ref: (value: unknown) => ({ value }),
    reactive: (value: unknown) => value,
    computed: (value: any) => Object.defineProperty({}, 'value', {
      get: typeof value === 'function' ? value : value.get,
      set: typeof value === 'function' ? undefined : value.set,
    }),
    watch() {}, onMounted() {}, onBeforeUnmount: (callback: () => void) => unmount.push(callback),
    useI18n: () => ({ t: (key: string) => key }),
    attachBrowserGamepadCapture: () => ({ release() {}, stop() {} }),
    applyGamepadFeedback() {},
  } as any;
  runInNewContext(viewCode, view, { filename: 'production-browser-stream-view.js' });
  view.loading.value = false;
  view.hostCapabilities.value = {
    ...service.unavailableHostCapabilities, enabled: true, availability: { state: 'ready' },
    codecs: { h264: { supported: true, hdr: false } },
  };
  view.browserCapabilities.value.h264.supported = true;
  assert.equal(view.startDisabled.value, false, 'the ordinary Start action is available');
  t.after(async () => {
    response.resolve(blocked === 'create' ? { session: { id: 'session-1' } } : answer);
    nextAnswer.resolve(answer);
    deleteResponse.resolve();
    closeResponse.resolve({ status: true });
    statusResponse.resolve({ status: true });
    await view.disconnect(false);
    for (const restore of restoreGlobals) restore();
  });
  return {
    view, peers, timers, deletes, reached, response, nextOfferReached, nextAnswer,
    deleteReached, deleteResponse,
    closeReached, closeResponse, statusReached, statusResponse,
    created: () => created,
    holdNextAnswer: () => { holdNextAnswer = true; },
    holdDelete: () => { holdDelete = true; },
    holdStatus: () => { holdStatus = true; },
    unmount: () => unmount.forEach((callback) => callback()),
    fullscreen: () => { view.nativeFullscreen.value = true; document.fullscreenElement = {}; },
    fullscreenExits: () => fullscreenExits,
  };
}

for (const blocked of ['create', 'offer'] as const) {
  test(`late ${blocked} cancellation preserves the replacement stream and its input`, async (t) => {
    const h = setup(t, blocked);
    const oldConnect = h.view.connect(false);
    await h.reached.promise;
    await h.view.disconnect();
    await h.view.connect(false);
    h.peers.at(-1)!.connected();
    h.fullscreen();
    assert.equal(h.view.inputReady.value, true);
    h.response.resolve(blocked === 'create' ? { session: { id: 'session-1' } } : answer);
    await oldConnect;
    assert.equal(h.view.connectionState.value, 'connected');
    assert.equal(h.view.inputReady.value, true, 'keyboard/mouse forwarding stays enabled');
    assert.equal(h.fullscreenExits(), 0, 'old cancellation does not exit the new fullscreen stream');
    assert.ok(h.deletes.some((url) => url.endsWith('/session-1')), 'the old server session is cleaned up');
  });
}

test('a late create failure cannot replace a new connection with an error', async (t) => {
  const h = setup(t);
  const oldConnect = h.view.connect(false);
  await h.reached.promise;
  await h.view.disconnect();
  await h.view.connect(false);
  h.peers.at(-1)!.connected();
  h.response.reject(new Error('old request failed'));
  await oldConnect;
  assert.equal(h.view.connectionState.value, 'connected');
  assert.equal(h.view.streamError.value, '');
});

test('old connect finalization cannot enable Start during the replacement negotiation', async (t) => {
  const h = setup(t);
  const oldConnect = h.view.connect(false);
  await h.reached.promise;
  await h.view.disconnect();
  h.holdNextAnswer();
  const nextConnect = h.view.connect(false);
  await h.nextOfferReached.promise;
  h.response.resolve({ session: { id: 'session-1' } });
  await oldConnect;
  assert.equal(h.view.connectionState.value, 'connecting');
  assert.equal(h.view.isConnecting.value, true);
  assert.equal(h.view.startDisabled.value, true);
  h.nextAnswer.resolve(answer);
  await nextConnect;
  assert.equal(h.view.isConnecting.value, false, 'the current completion clears its own pending flag');
});

test('late cancellation after unmount does not restart session status polling', async (t) => {
  const h = setup(t);
  const oldConnect = h.view.connect(false);
  await h.reached.promise;
  h.unmount();
  h.response.resolve({ session: { id: 'session-1' } });
  await oldConnect;
  assert.equal(h.timers.size, 0, 'unmounted view retains no polling timers');
});

test('a current connection failure still reports its error and resumes status polling', async (t) => {
  const h = setup(t);
  const connect = h.view.connect(false);
  await h.reached.promise;
  h.response.reject(new Error('current request failed'));
  await connect;
  assert.equal(h.view.connectionState.value, 'failed');
  assert.equal(h.view.streamError.value, 'current request failed');
  assert.equal(h.view.isConnecting.value, false);
  assert.ok([...h.timers.values()].some((timer) => timer.ms === 5000));
});

test('an earlier Disconnect response cannot detach a replacement after repeated Cancel', async (t) => {
  const h = setup(t, 'offer');
  const oldConnect = h.view.connect(false);
  await h.reached.promise;
  h.holdDelete();
  const firstDisconnect = h.view.disconnect();
  await h.deleteReached.promise;
  await h.view.disconnect();
  assert.equal(h.view.startDisabled.value, false);
  await h.view.connect(false);
  h.peers.at(-1)!.connected();
  const stream = {};
  h.view.videoEl.value = { srcObject: stream };
  h.view.audioEl.value = { srcObject: stream };
  h.deleteResponse.resolve();
  await firstDisconnect;
  assert.equal(h.view.connectionState.value, 'connected');
  assert.equal(h.view.videoEl.value.srcObject, stream);
  assert.equal(h.view.audioEl.value.srcObject, stream);
  h.response.resolve(answer);
  await oldConnect;
});

for (const restart of [false, true]) {
  test(`an app-close response after unmount cannot resume work (restart=${restart})`, async (t) => {
    const h = setup(t, 'none');
    h.view.startAfterTerminate.value = restart;
    const closing = h.view.confirmTerminate();
    await h.closeReached.promise;
    h.unmount();
    h.closeResponse.resolve({ status: true });
    await closing;
    assert.equal(h.created(), 0, 'leaving the page must not launch a browser session');
    assert.equal(h.timers.size, 0, 'leaving the page must not restart polling');
  });
}

test('unmount during restart cleanup cannot launch a replacement stream', async (t) => {
  const h = setup(t, 'none');
  await h.view.connect(false);
  h.view.startAfterTerminate.value = true;
  h.holdDelete();
  const closing = h.view.confirmTerminate();
  await h.closeReached.promise;
  h.closeResponse.resolve({ status: true });
  await h.deleteReached.promise;
  h.unmount();
  h.deleteResponse.resolve();
  await closing;
  assert.equal(h.created(), 1, 'the retired view must not replace its first session');
  assert.equal(h.timers.size, 0);
});

test('unmount during the post-close status request cancels the pending restart', async (t) => {
  const h = setup(t, 'none');
  h.view.startAfterTerminate.value = true;
  h.holdStatus();
  const closing = h.view.confirmTerminate();
  await h.closeReached.promise;
  h.closeResponse.resolve({ status: true });
  await h.statusReached.promise;
  h.unmount();
  h.statusResponse.resolve({ status: true });
  await closing;
  assert.equal(h.created(), 0);
  assert.equal(h.timers.size, 0);
});

test('a late app-close failure cannot exit another page\'s fullscreen', async (t) => {
  const h = setup(t, 'none');
  h.view.startAfterTerminate.value = true;
  const closing = h.view.confirmTerminate();
  await h.closeReached.promise;
  h.unmount();
  h.fullscreen(); // the destination page has entered fullscreen
  h.closeResponse.reject(new Error('retired close failed'));
  await closing;
  assert.equal(h.fullscreenExits(), 0);
  assert.equal(h.created(), 0);
});

test('unmount releases fullscreen reserved for a pending restart', async (t) => {
  const h = setup(t, 'none');
  h.fullscreen();
  h.view.startAfterTerminate.value = true;
  const closing = h.view.confirmTerminate();
  await h.closeReached.promise;
  h.unmount();
  assert.equal(h.fullscreenExits(), 1, 'navigation ends the restart fullscreen reservation');
  h.closeResponse.resolve({ status: true });
  await closing;
});

for (const restart of [false, true]) {
  test(`a mounted app-close action still completes (restart=${restart})`, async (t) => {
    const h = setup(t, 'none');
    h.view.startAfterTerminate.value = restart;
    const closing = h.view.confirmTerminate();
    await h.closeReached.promise;
    h.closeResponse.resolve({ status: true });
    await closing;
    assert.equal(h.created(), restart ? 1 : 0);
    assert.equal(h.view.sessionActionPending.value, false);
    assert.equal(h.view.sessionActionError.value, '');
  });
}

test('a mounted app-close failure is reported without launching a stream', async (t) => {
  const h = setup(t, 'none');
  h.view.startAfterTerminate.value = true;
  const closing = h.view.confirmTerminate();
  await h.closeReached.promise;
  h.closeResponse.reject(new Error('current close failed'));
  await closing;
  assert.equal(h.created(), 0);
  assert.equal(h.view.sessionActionPending.value, false);
  assert.equal(h.view.sessionActionError.value, 'current close failed');
});
