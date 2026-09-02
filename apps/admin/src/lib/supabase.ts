import { createBrowserClient } from "@supabase/ssr";

/**
 * Supabase client for Client Components.
 *
 * The server-side client lives in `supabase-server.ts` — keeping them in
 * separate modules matters, because importing `next/headers` into a module that
 * a Client Component touches breaks the build.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}
