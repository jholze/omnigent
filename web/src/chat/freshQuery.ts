/**
 * Fetch a team-chat query even when an older request is still in flight.
 *
 * `fetchQuery({ staleTime: 0 })` joins that older request. A bootstrap or a
 * message page started before a DM existed can then finish last and hide the
 * new row (AC-021a). Cancel the in-flight request, then fetch again.
 */
import type { QueryClient, QueryKey } from "@tanstack/react-query";

export function fetchLatest<T>(
  queryClient: QueryClient,
  queryKey: QueryKey,
  queryFn: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  // Cancel first so `fetchQuery` does not join the in-flight request. The
  // caller writes anything that must survive after this promise resolves:
  // the default revert restores the cache from when that request started.
  return queryClient.cancelQueries({ queryKey, exact: true }).then(() =>
    queryClient.fetchQuery({
      queryKey,
      queryFn: ({ signal }) => queryFn(signal),
      staleTime: 0,
    }),
  );
}
