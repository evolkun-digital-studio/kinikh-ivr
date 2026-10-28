interface TranscriptTurn {
  speaker: string;
  message: string;
}

function parseTranscript(raw: string): TranscriptTurn[] {
  const lines = raw.split(/\r?\n/).filter((l) => l.trim().length > 0);
  const turns: TranscriptTurn[] = [];

  for (const line of lines) {
    const match = line.match(/^\s*(Caller|AI|User|Assistant)\s*:\s*(.*)$/i);
    if (match) {
      turns.push({ speaker: match[1], message: match[2] });
    } else if (turns.length > 0) {
      // Continuation of the previous turn's message.
      turns[turns.length - 1].message += ` ${line.trim()}`;
    } else {
      turns.push({ speaker: "Caller", message: line.trim() });
    }
  }
  return turns;
}

export function TranscriptView({ transcript }: { transcript: string }) {
  const turns = parseTranscript(transcript);

  return (
    <div className="space-y-4">
      {turns.map((turn, i) => {
        const isAi = /^(ai|assistant)$/i.test(turn.speaker);
        return (
          <div key={i} className="flex gap-3">
            <div
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                isAi ? "bg-accent-soft text-accent-hover" : "bg-neutral-100 text-ink-muted"
              }`}
            >
              {isAi ? "AI" : "C"}
            </div>
            <div>
              <p className="text-xs font-medium text-ink-faint">{isAi ? "AI" : "Caller"}</p>
              <p className="mt-0.5 text-sm text-ink">{turn.message}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
