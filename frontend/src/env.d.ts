/// <reference types="vite/client" />

/**
 * The environment variables this app reads. Declaring them means a typo in
 * `import.meta.env.VITE_CLREK_...` is a compile error rather than `undefined`
 * at runtime.
 */
interface ImportMetaEnv {
  /** Clerk publishable key, `pk_test_…`. Safe in the browser by design. */
  readonly VITE_CLERK_PUBLISHABLE_KEY: string;
  /** FastAPI origin. Defaults to localhost:8000 when unset. */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
