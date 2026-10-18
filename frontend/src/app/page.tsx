const systemSignals = [
  { label: "AST PARSER", value: "READY", tone: "text-emerald-400" },
  { label: "GRAPH STORE", value: "STANDBY", tone: "text-amber-400" },
  { label: "CRAG SANDBOX", value: "ISOLATED", tone: "text-blue-400" },
];

export default function HomePage() {
  return (
    <main className="min-h-screen bg-canvas text-primaryText">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col px-6 py-5 lg:px-10">
        <header className="flex items-center justify-between border-b border-hairline pb-5">
          <div className="flex items-center gap-3">
            <span className="grid h-8 w-8 place-items-center rounded-md border border-active bg-active/15 font-mono text-xs font-bold text-indigo-300">
              C
            </span>
            <div>
              <p className="font-mono text-sm font-semibold tracking-[0.18em]">C.O.R.E.</p>
              <p className="font-mono text-[10px] tracking-[0.16em] text-mutedText">GRAPH REFACTORING SYSTEM</p>
            </div>
          </div>
          <span className="rounded-md border border-hairline bg-panel px-3 py-2 font-mono text-xs text-secondaryText">
            v0.1 / LOCAL
          </span>
        </header>

        <section className="grid flex-1 items-center gap-14 py-16 lg:grid-cols-[1.15fr_0.85fr]">
          <div>
            <p className="mb-5 font-mono text-xs uppercase tracking-[0.28em] text-indigo-300">
              Codebase intelligence / 001
            </p>
            <h1 className="max-w-3xl text-4xl font-semibold leading-tight tracking-[-0.03em] md:text-6xl">
              Make monolith boundaries visible before you move them.
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-7 text-secondaryText">
              C.O.R.E. builds a deterministic dependency graph, measures seam
              cohesion, predicts blast radius, and verifies extracted services
              before a pull request is opened.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a
                href="/dashboard"
                className="rounded-md bg-indigo-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-indigo-500"
              >
                Open workspace
              </a>
              <span className="rounded-md border border-hairline bg-panel px-5 py-3 font-mono text-xs text-secondaryText">
                sample_monolith included
              </span>
            </div>
          </div>

          <div className="rounded-lg border border-hairline bg-panel p-5 shadow-2xl shadow-black/20">
            <div className="mb-6 flex items-center justify-between border-b border-hairline pb-4">
              <span className="font-mono text-xs text-secondaryText">SYSTEM SIGNALS</span>
              <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_12px_#22c55e]" />
            </div>
            <div className="space-y-3">
              {systemSignals.map((signal) => (
                <div key={signal.label} className="flex items-center justify-between rounded-md border border-hairline bg-surface px-4 py-3">
                  <span className="font-mono text-xs text-mutedText">{signal.label}</span>
                  <span className={`font-mono text-xs font-semibold ${signal.tone}`}>{signal.value}</span>
                </div>
              ))}
            </div>
            <div className="mt-6 rounded-md border border-dashed border-hairline p-4">
              <p className="font-mono text-[11px] leading-5 text-mutedText">
                // ingest a repository to initialize the topology workspace
                <br />
                // all graph operations remain tenant-scoped
              </p>
            </div>
          </div>
        </section>

        <footer className="flex flex-wrap gap-6 border-t border-hairline pt-5 font-mono text-[10px] uppercase tracking-[0.14em] text-mutedText">
          <span>Tree-sitter AST</span>
          <span>Louvain seams</span>
          <span>1–5 hop impact</span>
          <span>CRAG verified extraction</span>
        </footer>
      </div>
    </main>
  );
}
