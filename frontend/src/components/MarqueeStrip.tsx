interface MarqueeStripProps {
  text?: string;
}

export function MarqueeStrip({
  text = "Sync JustWin • go/no-go review • protect win quality • reduce manual load • keep approval loop • public-sector guardrails • ",
}: MarqueeStripProps) {
  return (
    <div className="marquee shell-marquee text-[11px] uppercase tracking-[0.34em] text-zo-text-muted">
      <div className="marquee-track">{text}</div>
    </div>
  );
}
