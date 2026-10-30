import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyTypingFrame,
  resetTypingStore,
  TYPING_HIDE_MS,
  typingLine,
  useChannelTypers,
  useTypingStore,
  type ChatTypingFrame,
} from "./typingStore";

const ANNA = "anna@team-chat.test";
const JENS = "jens@team-chat.test";

function frame(principalId: string, active: boolean, channelId = "c-general"): ChatTypingFrame {
  return {
    type: "chat",
    kind: "typing",
    channel_id: channelId,
    principal_id: principalId,
    active,
  };
}

function typers(channelId = "c-general"): string[] | undefined {
  return useTypingStore.getState().byChannel[channelId];
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  resetTypingStore();
  vi.useRealTimers();
});

describe("typingLine", () => {
  it("AC-032a nobody typing is no line", () => {
    expect(typingLine([])).toBeNull();
  });

  it("AC-032a one name reads '{name} schreibt…'", () => {
    expect(typingLine(["Anna"])).toBe("Anna schreibt…");
  });

  it("AC-032a two names read '{a} und {b} schreiben…'", () => {
    expect(typingLine(["Anna", "Jens"])).toBe("Anna und Jens schreiben…");
  });

  it("AC-032a three or more names collapse to 'Mehrere Personen schreiben…'", () => {
    expect(typingLine(["Anna", "Jens", "Bob"])).toBe("Mehrere Personen schreiben…");
  });
});

describe("typing store", () => {
  it("AC-032a active:true lists the typist in that channel only", () => {
    applyTypingFrame(frame(ANNA, true));

    expect(typers()).toEqual([ANNA]);
    expect(typers("c-other")).toBeUndefined();
  });

  it("AC-032a active:false removes the typist at once", () => {
    applyTypingFrame(frame(ANNA, true));
    applyTypingFrame(frame(ANNA, false));

    expect(typers()).toBeUndefined();
  });

  it("AC-032a active:true disappears after TYPING_HIDE_MS without another frame", () => {
    applyTypingFrame(frame(ANNA, true));

    vi.advanceTimersByTime(TYPING_HIDE_MS - 1);
    expect(typers()).toEqual([ANNA]);
    vi.advanceTimersByTime(1);
    expect(typers()).toBeUndefined();
  });

  it("AC-032a a fresh active:true restarts the hide timer", () => {
    applyTypingFrame(frame(ANNA, true));
    vi.advanceTimersByTime(3000);
    applyTypingFrame(frame(ANNA, true));
    vi.advanceTimersByTime(TYPING_HIDE_MS - 1);

    expect(typers()).toEqual([ANNA]);
  });

  it("AC-032a useChannelTypers hides the viewer's own id", () => {
    applyTypingFrame(frame(ANNA, true));
    applyTypingFrame(frame(JENS, true));

    const { result } = renderHook(() => useChannelTypers("c-general", ANNA));

    expect(result.current).toEqual([JENS]);
  });

  it("AC-032a useChannelTypers is empty when only the viewer is typing", () => {
    const { result } = renderHook(() => useChannelTypers("c-general", ANNA));
    act(() => applyTypingFrame(frame(ANNA, true)));

    expect(result.current).toEqual([]);
  });
});
