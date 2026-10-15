import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test, type TestContext } from 'node:test';
import { runInNewContext } from 'node:vm';

// Execute the entire legacy view script. Vue scheduling, HTTP, and the client
// are boundaries; the production client has its own delayed-HTTP regressions.
const source = readFileSync(
  new URL('../../web-legacy/views/WebRtcClientView.vue', import.meta.url),
  'utf8',
);
const script = source.split('<script setup lang="ts">')[1].split('</script>')[0];
const code =
  stripTypeScriptTypes(script).replace(/^import\s[\s\S]*?from\s+'[^']+';\s*/gm, '') +
  '\nObject.assign(globalThis, { startConnect, connect, disconnect, confirmTerminateAndConnect, fetchSessionStatus, sessionId, serverSession, sessionStatus, isConnecting, isConnected, connectionState, videoEl, audioEl, activeNotification });';

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function setup(t: TestContext) {
  let timerId = 0;
  const timers = new Map<number, { callback: () => void; ms: number }>();
  const schedule = (callback: () => void, ms: number) => {
    timers.set(++timerId, { callback, ms });
    return timerId;
  };
  const mounted: Array<() => Promise<void>> = [];
  const unmounted: Array<() => void> = [];
  const dialogs: Array<{ onPositiveClick: () => Promise<void> }> = [];
  const calls: Array<{ result: ReturnType<typeof deferred<string>>; callbacks: any }> = [];
  const entered = deferred();
  let tick = Promise.resolve();
  let deletion = Promise.resolve();
  let apps = Promise.resolve();
  let status: Promise<unknown> = Promise.resolve({ status: 200, data: { status: true } });
  let server: (id: string) => Promise<unknown> = async (id) => ({ session: { id } });
  let closeRequests = 0;
  const client = {
    connect(_config: unknown, callbacks: unknown) {
      const result = deferred<string>();
      calls.push({ result, callbacks });
      entered.resolve();
      return result.promise;
    },
    disconnect: () => deletion,
    setAudioLatencyTargets() {},
    setVideoLatencyTarget() {},
    setInputPriority() {},
  };
  const window = {
    setTimeout: schedule,
    clearTimeout: (id: number) => timers.delete(id),
    setInterval: schedule,
    clearInterval: (id: number) => timers.delete(id),
    addEventListener() {},
    removeEventListener() {},
  };
  const view: any = {
    window,
    document: {
      addEventListener() {},
      removeEventListener() {},
      visibilityState: 'visible',
      hasFocus: () => true,
    },
    navigator: {},
    console: { error() {} },
    Error,
    clearTimeout: window.clearTimeout,
    requestAnimationFrame: (callback: () => void) => queueMicrotask(callback),
    ref: (value: unknown) => ({ value }),
    reactive: (value: unknown) => value,
    computed: (value: any) =>
      Object.defineProperty({}, 'value', {
        get: typeof value === 'function' ? value : value.get,
        set: typeof value === 'function' ? undefined : value.set,
      }),
    watch() {},
    nextTick: () => tick,
    onMounted: (callback: () => Promise<void>) => mounted.push(callback),
    onBeforeUnmount: (callback: () => void) => unmounted.push(callback),
    useI18n: () => ({ t: (key: string) => key, locale: { value: 'en' } }),
    useDialog: () => ({ warning: (options: any) => dialogs.push(options) }),
    useMessage: () => ({}),
    useAppsStore: () => ({ loadApps: () => apps }),
    storeToRefs: () => ({ apps: { value: [] } }),
    WebRtcHttpApi: class {
      getSessionState(id: string) {
        return server(id);
      }
    },
    WebRtcClient: class {
      constructor() {
        return client;
      }
    },
    WebRtcConnectionCanceledError: class extends Error {},
    releaseKeyboardLock() {},
    requestKeyboardLock() {},
    applyGamepadFeedback() {},
    http: {
      get: () => status,
      async post() {
        ++closeRequests;
      },
    },
  };
  runInNewContext(code, view, { filename: 'production-legacy-browser-stream-view.js' });
  t.after(() => {
    unmounted.forEach((callback) => callback());
  });
  return {
    view,
    timers,
    calls,
    entered,
    dialogs,
    unmount: () => unmounted.forEach((callback) => callback()),
    mount: () => Promise.all(mounted.map((callback) => callback())),
    holdTick: (value: Promise<void>) => {
      tick = value;
    },
    holdDelete: (value: Promise<void>) => {
      deletion = value;
    },
    holdApps: (value: Promise<void>) => {
      apps = value;
    },
    holdStatus: (value: Promise<unknown>) => {
      status = value;
    },
    server: (value: typeof server) => {
      server = value;
    },
    closeRequests: () => closeRequests,
    async connect(id: string) {
      const previous = calls.length;
      const connecting = view.startConnect();
      // Drain nextTick and the two spinner frames, without advancing timers.
      await new Promise<void>((resolve) => setImmediate(resolve));
      assert.equal(calls.length, previous + 1);
      calls.at(-1)!.result.resolve(id);
      await connecting;
    },
  };
}

test('legacy view: unmount during the spinner cannot start a session', async (t) => {
  const h = setup(t);
  const tick = deferred();
  h.holdTick(tick.promise);
  const connecting = h.view.startConnect();
  h.unmount();
  tick.resolve();
  await new Promise<void>((resolve) => setImmediate(resolve));
  for (const call of h.calls) call.result.resolve('unwanted');
  await connecting;
  assert.equal(h.calls.length, 0);
  assert.equal(h.timers.size, 0);
});

test('legacy view: a late connection rejection after unmount cannot notify or poll', async (t) => {
  const h = setup(t);
  const connecting = h.view.startConnect();
  await h.entered.promise;
  h.unmount();
  h.calls[0].result.reject(new Error('late cancellation'));
  await connecting;
  assert.equal(h.view.activeNotification.value, null);
  assert.equal(h.timers.size, 0);
});

test('legacy view: an old DELETE completion cannot clear replacement media or state', async (t) => {
  const h = setup(t);
  const deletion = deferred();
  h.holdDelete(deletion.promise);
  const closing = h.view.disconnect();
  await h.connect('replacement');
  h.view.isConnected.value = true;
  h.view.connectionState.value = 'connected';
  const stream = {};
  h.view.videoEl.value = { srcObject: stream };
  h.view.audioEl.value = { srcObject: stream };
  deletion.resolve();
  await closing;
  assert.equal(h.view.sessionId.value, 'replacement');
  assert.equal(h.view.connectionState.value, 'connected');
  assert.equal(h.view.videoEl.value.srcObject, stream);
  assert.equal(h.view.audioEl.value.srcObject, stream);
});

test('legacy view: old connect finalization cannot clear a replacement spinner', async (t) => {
  const h = setup(t);
  const old = h.view.startConnect();
  await h.entered.promise;
  await h.view.disconnect();
  const next = h.view.startConnect();
  await new Promise<void>((resolve) => setImmediate(resolve));
  h.calls[0].result.reject(new Error('old cancellation'));
  await old;
  assert.equal(h.view.isConnecting.value, true);
  assert.equal(h.view.activeNotification.value, null);
  h.calls[1].result.resolve('replacement');
  await next;
  assert.equal(h.view.isConnecting.value, false);
});

test('legacy view: delayed app confirmation after unmount cannot terminate or reconnect', async (t) => {
  const h = setup(t);
  await h.view.confirmTerminateAndConnect();
  h.unmount();
  const confirmation = h.dialogs[0].onPositiveClick();
  await new Promise<void>((resolve) => setImmediate(resolve));
  for (const call of h.calls) call.result.resolve('unwanted');
  await confirmation;
  assert.equal(h.closeRequests(), 0);
  assert.equal(h.calls.length, 0);
  assert.equal(h.timers.size, 0);
});

test('legacy view: app loading completed after unmount cannot restart polling', async (t) => {
  const h = setup(t);
  const apps = deferred();
  h.holdApps(apps.promise);
  const mounting = h.mount();
  h.unmount();
  apps.resolve();
  await mounting;
  assert.equal(h.timers.size, 0);
});

test('legacy view: old server state cannot overwrite a replacement session', async (t) => {
  const h = setup(t);
  const state = deferred<unknown>();
  h.server(async (id) => (id === 'old' ? state.promise : { session: { id } }));
  await h.connect('old');
  await h.view.disconnect();
  await h.connect('replacement');
  state.resolve({ session: { id: 'old' } });
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(h.view.serverSession.value.id, 'replacement');
});

test('legacy view: current failure still reports an error and polls session status', async (t) => {
  const h = setup(t);
  const connecting = h.view.startConnect();
  await h.entered.promise;
  h.calls[0].result.reject(new Error('current failure'));
  await connecting;
  assert.equal(h.view.activeNotification.value.message, 'current failure');
  assert.equal(h.view.isConnecting.value, false);
  assert.ok([...h.timers.values()].some((timer) => timer.ms === 5000));
});

test('legacy view: current success starts server polling', async (t) => {
  const h = setup(t);
  await h.connect('current');
  assert.equal(h.view.sessionId.value, 'current');
  assert.equal(h.view.serverSession.value.id, 'current');
  assert.ok([...h.timers.values()].some((timer) => timer.ms === 1000));
});

test('legacy view: a status response after unmount cannot update the retired view', async (t) => {
  const h = setup(t);
  const status = deferred<unknown>();
  h.holdStatus(status.promise);
  const fetching = h.view.fetchSessionStatus();
  h.unmount();
  status.resolve({ status: 200, data: { status: true, appRunning: true } });
  await fetching;
  assert.equal(h.view.sessionStatus.value, null);
});
