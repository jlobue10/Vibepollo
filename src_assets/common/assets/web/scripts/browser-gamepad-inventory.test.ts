import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test, type TestContext } from 'node:test';

// Execute both complete capture modules with a fake Gamepad API and animation
// clock. Legacy's non-type import is used only for types, so erase it here too.
async function captureModule(path: string) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8').replace(
    /^import(?: type)? \{ GamepadFeedbackMessage, InputMessage \} from '@\/types\/webrtc';\r?\n/,
    '',
  );
  const code = stripTypeScriptTypes(source);
  return import(`data:text/javascript,${encodeURIComponent(code)}`);
}

const legacy = await captureModule('../../web-legacy/utils/webrtc/input.ts');
const current = await captureModule('../utils/webrtc/browserGamepad.ts');

function pad(index: number): Gamepad {
  return {
    index,
    id: 'Xbox Controller',
    connected: true,
    mapping: 'standard',
    axes: [0, 0, 0, 0],
    buttons: Array.from({ length: 17 }, (_, i) => ({
      pressed: i === 0,
      touched: false,
      value: i === 0 ? 1 : 0,
    })),
    timestamp: 0,
    vibrationActuator: {
      playEffect: async () => 'complete',
      reset: async () => 'complete',
    },
  };
}

function setup(t: TestContext, version: 'legacy' | 'v2', initial: Gamepad[]) {
  let pads: Array<Gamepad | null> = initial;
  let time = 1000;
  let nextFrame = 0;
  const frames = new Map<number, FrameRequestCallback>();
  const requestAnimationFrame = (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  };
  const cancelAnimationFrame = (id: number) => {
    frames.delete(id);
  };
  const globals = {
    window: Object.assign(new EventTarget(), { requestAnimationFrame, cancelAnimationFrame }),
    document: Object.assign(new EventTarget(), { visibilityState: 'visible' }),
    navigator: { getGamepads: () => pads },
    performance: { now: () => time },
    requestAnimationFrame,
    cancelAnimationFrame,
  };
  const restore: Array<() => void> = [];
  for (const [key, value] of Object.entries(globals)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, value });
    restore.push(() => {
      if (previous) Object.defineProperty(globalThis, key, previous);
      else Reflect.deleteProperty(globalThis, key);
    });
  }
  const messages: Array<Record<string, any>> = [];
  const send = (message: Record<string, any>) => {
    messages.push(message);
    return true;
  };
  const stop =
    version === 'legacy'
      ? legacy.attachInputCapture(new EventTarget(), (payload: string) => send(JSON.parse(payload)))
      : current.attachBrowserGamepadCapture(send).stop;
  t.after(() => {
    stop();
    assert.equal(frames.size, 0, 'capture cleanup cancels its pending frame');
    for (const reset of restore) reset();
  });
  return {
    messages,
    setPads(value: Array<Gamepad | null>) {
      pads = value;
    },
    frame(delta = 16) {
      time += delta;
      const pending = [...frames.values()];
      frames.clear();
      for (const callback of pending) callback(time);
    },
  };
}

for (const version of ['legacy', 'v2'] as const) {
  test(`${version}: every initial state contains the complete controller inventory`, (t) => {
    const { messages, frame } = setup(t, version, [pad(0), pad(1)]);
    frame();
    assert.deepEqual(
      messages.filter((m) => m.type === 'gamepad_state').map((m) => [m.id, m.activeMask]),
      [
        [0, 3],
        [1, 3],
      ],
    );
  });

  test(`${version}: heartbeat packets cannot remove another connected controller`, (t) => {
    const { messages, frame } = setup(t, version, [pad(0), pad(1)]);
    frame();
    messages.length = 0;
    frame(500);
    assert.deepEqual(
      messages.filter((m) => m.type === 'gamepad_state').map((m) => [m.id, m.activeMask]),
      [
        [0, 3],
        [1, 3],
      ],
    );
    assert.equal(messages.filter((m) => m.type === 'gamepad_connect').length, 0);
  });

  test(`${version}: inventory uses controller indices across holes and ignored slots`, (t) => {
    const { messages, frame, setPads } = setup(t, version, []);
    const disconnected = { ...pad(2), connected: false };
    setPads([null, pad(1), disconnected, null, pad(15), pad(16)]);
    frame();
    assert.deepEqual(
      messages.filter((m) => m.type === 'gamepad_state').map((m) => [m.id, m.activeMask]),
      [
        [1, 0x8002],
        [15, 0x8002],
      ],
    );
  });

  test(`${version}: removing one controller preserves the survivors in every packet`, (t) => {
    const { messages, frame, setPads } = setup(t, version, [pad(0), pad(1), pad(2)]);
    frame();
    messages.length = 0;
    setPads([null, pad(1), pad(2)]);
    frame(500);
    assert.deepEqual(
      messages.filter((m) => m.type === 'gamepad_state').map((m) => [m.id, m.activeMask]),
      [
        [1, 6],
        [2, 6],
      ],
    );
    assert.deepEqual(
      messages.filter((m) => m.type === 'gamepad_disconnect').map((m) => [m.id, m.activeMask]),
      [[0, 6]],
    );
  });
}
