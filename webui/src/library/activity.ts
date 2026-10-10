import { useCallback, useEffect, useRef, useState } from "react";
import { libraryRequest } from "./api";
import { LibraryActivity } from "./model";

export interface ActivityCommand {
  action: "favorite" | "open" | "progress" | "remove" | "clear" | "pause";
  path?: string;
  value?: boolean;
  position?: number;
  duration?: number;
  history_kind?: "opened" | "watched" | "all";
  scope?: string;
}
interface ActivityResponse {
  paused: boolean;
  activity: LibraryActivity;
}

/** Serialize mutations across views, including final progress on player unmount. */
let writes: Promise<unknown> = Promise.resolve();
export async function flushActivityWrites() {
  await writes.catch(() => undefined);
}
export function writeActivity(
  command: ActivityCommand,
): Promise<ActivityResponse> {
  const operation = writes
    .catch(() => undefined)
    .then(() =>
      libraryRequest<ActivityResponse>("/api/library/activity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(command),
        keepalive: true,
      }),
    );
  writes = operation;
  return operation;
}

export function useLibraryActivity() {
  const [overrides, setOverrides] = useState<Record<string, LibraryActivity>>(
    {},
  );
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const version = useRef(0);
  const locked = useRef(new Map<string, number>());
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const mutate = useCallback(async (command: ActivityCommand) => {
    const key = command.path || command.action;
    if (command.action === "favorite" && locked.current.has(key)) return false;
    version.current++;
    locked.current.set(key, (locked.current.get(key) || 0) + 1);
    if (alive.current) setPending(new Set(locked.current.keys()));
    try {
      const result = await writeActivity(command);
      if (alive.current) {
        setError("");
        setPaused(result.paused);
        if (command.action === "clear") setOverrides({});
        else if (command.path)
          setOverrides((values) => ({
            ...values,
            [command.path!]: result.activity,
          }));
      }
      return true;
    } catch (reason) {
      if (alive.current)
        setError(reason instanceof Error ? reason.message : "记录保存失败");
      return false;
    } finally {
      version.current++;
      const count = (locked.current.get(key) || 1) - 1;
      if (count) locked.current.set(key, count);
      else locked.current.delete(key);
      if (alive.current) setPending(new Set(locked.current.keys()));
    }
  }, []);
  const hydrate = useCallback(
    (files: { path: string }[], pausedValue: boolean, epoch: number) => {
      if (version.current !== epoch || locked.current.size) return;
      setPaused(pausedValue);
      setOverrides((values) => {
        const next = { ...values };
        files.forEach((entry) => delete next[entry.path]);
        return next;
      });
    },
    [],
  );
  return {
    forget: (path: string) => {
      version.current++;
      setOverrides((values) => {
        const next = { ...values };
        delete next[path];
        return next;
      });
      setError("");
    },
    overrides,
    pending,
    paused,
    setPaused,
    error,
    setError,
    mutate,
    version,
    hydrate,
  };
}
