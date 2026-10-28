import { useEffect, useRef, useState } from "react";
import { IconPause, IconPlay } from "@/components/navigation/icons";
import { authTokens } from "@/stores/authStore";

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Same fallback as services/api.ts: the production image builds with an
// empty VITE_API_BASE_URL and is served from the API's own origin by nginx.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin;

/**
 * Plays a recording through the authenticated `/recordings/{id}/audio`
 * endpoint (bearer header — native <audio src> can't attach one), fetched
 * as a blob into an object URL. Never touches a raw filesystem path.
 *
 * Loads lazily on first play, not on mount — this renders once per row in
 * the Recordings table, and eagerly fetching every row's full audio file
 * just to paint the list would be wasteful and slow.
 */
export function AudioPlayer({ recordingId }: { recordingId: string }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = async () => {
    setIsLoading(true);
    setError(false);
    try {
      const res = await fetch(`${BASE_URL}/api/v1/recordings/${recordingId}/audio`, {
        headers: { Authorization: `Bearer ${authTokens.getAccess() ?? ""}` },
      });
      if (!res.ok) throw new Error(String(res.status));
      const blob = await res.blob();
      setObjectUrl(URL.createObjectURL(blob));
    } catch {
      setError(true);
    } finally {
      setIsLoading(false);
    }
  };

  const toggle = async () => {
    if (error) return;
    if (!objectUrl) {
      await load();
      return; // audio.play() is triggered by the effect below once loaded
    }
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) audio.pause();
    else audio.play().catch(() => setError(true));
  };

  useEffect(() => {
    if (objectUrl && audioRef.current) {
      audioRef.current.play().catch(() => setError(true));
    }
  }, [objectUrl]);

  if (error) {
    return <p className="text-sm text-ink-muted">Audio file could not be loaded.</p>;
  }

  return (
    <div className="flex items-center gap-3 rounded-md border border-border bg-neutral-50 px-4 py-3">
      {objectUrl && (
        <audio
          ref={audioRef}
          src={objectUrl}
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onError={() => setError(true)}
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
          onTimeUpdate={(e) => setProgress(e.currentTarget.currentTime)}
        />
      )}
      <button
        onClick={toggle}
        disabled={isLoading}
        className="focus-ring flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-white hover:bg-ink/90 disabled:opacity-60"
        aria-label={isPlaying ? "Pause" : "Play"}
      >
        {isLoading ? (
          <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
        ) : isPlaying ? (
          <IconPause className="h-4 w-4" />
        ) : (
          <IconPlay className="h-4 w-4 translate-x-0.5" />
        )}
      </button>
      <div className="flex-1">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-200">
          <div
            className="h-full bg-accent"
            style={{ width: `${duration ? (progress / duration) * 100 : 0}%` }}
          />
        </div>
      </div>
      <span className="w-20 shrink-0 text-right text-xs tabular-nums text-ink-muted">
        {formatTime(progress)} / {formatTime(duration)}
      </span>
    </div>
  );
}
