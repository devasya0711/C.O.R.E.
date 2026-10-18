export function ConfigurationNotice() {
  return (
    <section
      aria-labelledby="auth-configuration-title"
      className="w-full rounded-lg border border-hairline bg-panel p-6"
    >
      <p className="font-mono text-xs uppercase tracking-[0.16em] text-secondaryText">
        AUTH_NOT_CONFIGURED
      </p>
      <h2 id="auth-configuration-title" className="mt-3 text-lg font-semibold">
        Authentication needs configuration
      </h2>
      <p className="mt-3 text-sm leading-6 text-secondaryText">
        Sign-in is unavailable until this deployment has its Clerk publishable key
        and server secret key configured. Contact the deployment administrator.
      </p>
      <p className="mt-4 border-t border-hairline pt-4 text-sm text-secondaryText">
        Workspace routes remain protected while authentication is unavailable.
      </p>
    </section>
  );
}
