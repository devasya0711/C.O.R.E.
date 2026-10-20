"use client";

export default function DashboardError({ reset }: { reset: () => void }) {
  return (
    <main className="grid min-h-screen place-items-center bg-canvas px-5 text-primaryText">
      <section className="w-full max-w-lg rounded-lg border border-red-500/30 bg-panel p-6">
        <p className="font-mono text-xs uppercase tracking-[0.16em] text-red-400">WORKSPACE_ERROR</p>
        <h1 className="mt-3 text-xl font-semibold">The workspace could not load.</h1>
        <p className="mt-3 text-sm leading-6 text-secondaryText">
          Retry the route. If the problem continues, check the API and authentication configuration.
        </p>
        <button
          type="button"
          onClick={reset}
          className="mt-5 rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500"
        >
          Retry workspace
        </button>
      </section>
    </main>
  );
}
