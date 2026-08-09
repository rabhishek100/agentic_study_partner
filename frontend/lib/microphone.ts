"use client";

/**
 * Which microphone dictation records from.
 *
 * One module-level store rather than component state, because a reader can
 * have several composers mounted at once — the main one and a floating side
 * chat over the same book — and choosing a headset in one of them must be the
 * choice everywhere. The selection outlives the session in `localStorage`:
 * plugging in a headset once and re-picking it on every reload is not a
 * choice, it is a chore.
 */

const SELECTED_KEY = "dictation:microphone";

export interface MicrophoneOption {
  deviceId: string;
  label: string;
}

export interface MicrophoneSnapshot {
  /** Empty until the browser has been asked for devices at least once. */
  devices: MicrophoneOption[];
  /** Null means whatever the system considers default. */
  selectedId: string | null;
}

const EMPTY: MicrophoneSnapshot = { devices: [], selectedId: null };

let snapshot: MicrophoneSnapshot = EMPTY;
const listeners = new Set<() => void>();
let watching = false;
// Refreshes overlap — one on mount, one when permission is granted, one per
// `devicechange` — and `enumerateDevices` does not promise to resolve in call
// order. Without this, a slow early answer can land last and reinstate a
// device list that has already been superseded.
let latestRefresh = 0;

function readSelection(): string | null {
  try {
    return window.localStorage.getItem(SELECTED_KEY) || null;
  } catch {
    // Blocked storage costs the reader a remembered device, nothing more.
    return null;
  }
}

function writeSelection(deviceId: string | null): void {
  try {
    if (deviceId) window.localStorage.setItem(SELECTED_KEY, deviceId);
    else window.localStorage.removeItem(SELECTED_KEY);
  } catch {
    // As above.
  }
}

function publish(next: MicrophoneSnapshot): void {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

function supportsEnumeration(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.enumerateDevices === "function"
  );
}

/**
 * Ask the browser what inputs exist.
 *
 * Labels are empty until the origin has been granted the microphone once, so
 * before the first recording this returns devices named only by position.
 * That is a platform rule, not a bug to work around — it is also why this is
 * called again after every successful `getUserMedia`, when the real names
 * become readable.
 */
export async function refreshMicrophones(): Promise<void> {
  if (!supportsEnumeration()) return;
  const token = ++latestRefresh;
  let found: MediaDeviceInfo[];
  try {
    found = await navigator.mediaDevices.enumerateDevices();
  } catch {
    return;
  }
  if (token !== latestRefresh) return;
  const devices = found
    .filter((device) => device.kind === "audioinput")
    // Chrome lists "default" and "communications" as aliases of a real input,
    // so keeping them would offer the same headset three times. This menu has
    // its own "System default" entry, which is what those aliases mean.
    .filter(
      (device) =>
        device.deviceId !== "default" && device.deviceId !== "communications",
    )
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label.trim() || `Microphone ${index + 1}`,
    }));

  // A device that has been unplugged cannot stay selected, or the next
  // recording would fail against an id nothing answers to.
  let selectedId = snapshot.selectedId;
  if (selectedId && !devices.some((device) => device.deviceId === selectedId)) {
    selectedId = null;
    writeSelection(null);
  }
  publish({ devices, selectedId });
}

export function subscribeMicrophones(listener: () => void): () => void {
  listeners.add(listener);
  if (!watching) {
    watching = true;
    publish({ devices: snapshot.devices, selectedId: readSelection() });
    void refreshMicrophones();
    navigator.mediaDevices?.addEventListener?.("devicechange", onDeviceChange);
  }
  return () => {
    listeners.delete(listener);
  };
}

function onDeviceChange(): void {
  void refreshMicrophones();
}

export function getMicrophoneSnapshot(): MicrophoneSnapshot {
  return snapshot;
}

/** The server has no devices to report, and must not read storage. */
export function getServerMicrophoneSnapshot(): MicrophoneSnapshot {
  return EMPTY;
}

export function selectMicrophone(deviceId: string | null): void {
  writeSelection(deviceId);
  publish({ devices: snapshot.devices, selectedId: deviceId });
}

/**
 * The audio constraint for the current choice.
 *
 * `exact` rather than a plain id: a soft constraint would quietly record from
 * the wrong microphone when the chosen one is missing, which is the one
 * outcome a device picker exists to prevent. The caller handles the
 * resulting `OverconstrainedError` by falling back to the default explicitly.
 */
export function microphoneConstraint(): MediaTrackConstraints | true {
  const { selectedId } = snapshot;
  return selectedId ? { deviceId: { exact: selectedId } } : true;
}

/**
 * Open the selected input with browser speech processing enabled.
 *
 * This is shared by the interview preflight and the live session so the mic
 * that passes the test is exactly the mic that records the answer. If a
 * remembered USB device disappeared, retry the system default and clear the
 * stale choice instead of leaving the interview silently listening to
 * nothing.
 */
export async function openSpeechMicrophone(): Promise<MediaStream> {
  const selected = microphoneConstraint();
  const processing = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: selected === true ? processing : { ...selected, ...processing },
    });
    await refreshMicrophones();
    return stream;
  } catch (failure) {
    const missing =
      selected !== true &&
      failure instanceof DOMException &&
      (failure.name === "OverconstrainedError" || failure.name === "NotFoundError");
    if (!missing) throw failure;
    selectMicrophone(null);
    const stream = await navigator.mediaDevices.getUserMedia({ audio: processing });
    await refreshMicrophones();
    return stream;
  }
}

/** Test seam: drop everything this module remembers. */
export function resetMicrophones(): void {
  snapshot = EMPTY;
  listeners.clear();
  watching = false;
  latestRefresh = 0;
}
