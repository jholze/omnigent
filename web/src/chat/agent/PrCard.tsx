/**
 * Read-only pull request card in an open Agent-Thread (FR-060).
 *
 * Same data as the right-rail Pull Requests tab (`usePullRequestInfo`,
 * `derivePullRequestPanelState` in `shell/PullRequestPanel.tsx`): title,
 * number, state, CI summary and a link out. Only a session with a workspace
 * asks; anything short of an associated PR (no git repo, provider app or CLI
 * not connected, not signed in, no PR, runner offline, request error)
 * renders nothing and no error (AC-060b). No writes to the provider.
 */
import {
  CircleCheckIcon,
  CircleDotIcon,
  CircleXIcon,
  ExternalLinkIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  GitPullRequestIcon,
  type LucideIcon,
} from "lucide-react";

import {
  normalizePullRequestInfo,
  usePullRequestInfo,
  type PullRequest,
} from "@/hooks/usePullRequests";
import { cn } from "@/lib/utils";
import { derivePullRequestPanelState } from "@/shell/PullRequestPanel";

export const PR_CARD_TEST_ID = "pr-card";

/** State as GitHub names it, lower case ("open", "merged", "closed", "draft"). */
export function prStateLabel(pr: Pick<PullRequest, "state" | "is_draft">): string {
  const state = pr.state.toLowerCase();
  if (state === "open" && pr.is_draft) return "draft";
  return state;
}

export type CiTone = "passing" | "failing" | "pending" | "none" | "unknown";

/** CI summary: any failing check is red, then running, then green. */
export function prCiStatus(pr: Pick<PullRequest, "checks" | "checks_supported">): {
  tone: CiTone;
  label: string;
} {
  if (pr.checks_supported === false) return { tone: "unknown", label: "unbekannt" };
  const { failing, pending, passing, total } = pr.checks;
  if (failing > 0) return { tone: "failing", label: "rot" };
  if (pending > 0) return { tone: "pending", label: "läuft" };
  if (passing > 0 || total > 0) return { tone: "passing", label: "grün" };
  return { tone: "none", label: "keine Checks" };
}

const CI_ICONS: Record<CiTone, { Icon: LucideIcon; className: string }> = {
  passing: { Icon: CircleCheckIcon, className: "text-success" },
  failing: { Icon: CircleXIcon, className: "text-destructive" },
  pending: { Icon: CircleDotIcon, className: "text-warning" },
  none: { Icon: CircleDotIcon, className: "text-muted-foreground" },
  unknown: { Icon: CircleDotIcon, className: "text-muted-foreground" },
};

function stateIcon(state: string): LucideIcon {
  if (state === "merged") return GitMergeIcon;
  if (state === "closed") return GitPullRequestClosedIcon;
  return GitPullRequestIcon;
}

export function PrCardView({ pr }: { pr: PullRequest }) {
  const state = prStateLabel(pr);
  const ci = prCiStatus(pr);
  const StateIcon = stateIcon(state);
  const { Icon: CiIcon, className: ciClass } = CI_ICONS[ci.tone];
  return (
    <section
      data-testid={PR_CARD_TEST_ID}
      data-state={state}
      data-ci={ci.tone}
      aria-label={`Pull Request #${pr.number}`}
      className="flex min-w-0 flex-col gap-1 rounded-md border border-border px-3 py-2 text-sm"
    >
      <div className="flex min-w-0 items-start gap-2">
        <StateIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <a
          href={pr.url}
          target="_blank"
          rel="noopener noreferrer"
          className="-my-1 flex min-h-11 min-w-0 flex-1 items-start gap-1 rounded-sm py-1 font-medium break-words text-pretty [overflow-wrap:anywhere] hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none md:min-h-0"
        >
          <span className="min-w-0">{pr.title}</span>
          <ExternalLinkIcon
            className="mt-0.5 size-3.5 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <span className="sr-only">(öffnet in neuem Tab)</span>
        </a>
        <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
          #{pr.number}
        </span>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 pl-6 text-xs text-muted-foreground">
        <span>
          Status <span className="font-medium text-foreground">{state}</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <CiIcon className={cn("size-3.5 shrink-0", ciClass)} aria-hidden="true" />
          CI <span className="font-medium text-foreground">{ci.label}</span>
        </span>
      </div>
    </section>
  );
}

export interface PrCardProps {
  sessionId: string;
  /** Session workspace; without one there is no repository to ask. */
  workspace: string | null | undefined;
}

export function PrCard({ sessionId, workspace }: PrCardProps) {
  const info = usePullRequestInfo(workspace ? sessionId : undefined);
  if (!workspace) return null;
  const panel = derivePullRequestPanelState({
    isLoading: info.isLoading,
    error: info.error,
    data: info.data,
  });
  if (panel.kind !== "ready" || info.data === undefined) return null;
  const pr = normalizePullRequestInfo(info.data).pr;
  if (!pr) return null;
  return <PrCardView pr={pr} />;
}
