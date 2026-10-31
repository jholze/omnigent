// Inline approval / option-picker card rendered when the server
// emits an MCP-shape `response.elicitation_request`.
//
// Render modes (decided in order):
//
//   - **ExitPlanMode plan review** — when the elicitation carries a
//     structured `exitPlanMode` payload (the PermissionRequest
//     endpoint stamps the full tool_input when the gated tool is
//     Claude's built-in ExitPlanMode). Renders the plan markdown
//     plus approve / approve-in-auto-mode / reject-with-feedback
//     actions — see `ExitPlanModeReview`.
//
//   - **AskUserQuestion form** — when the elicitation carries a
//     structured `askUserQuestion` payload (the PermissionRequest
//     endpoint stamps this when the gated tool is Claude's built-in
//     AskUserQuestion). Renders a multi-question form with radio
//     inputs for single-select, checkboxes for multi-select. Submit
//     posts the gathered answers as `content.answers`.
//
//   - **Option buttons** — when `requestedSchema` is
//     `{properties: {answer: {enum: [...]}}}`. Currently no
//     producer emits this for built-in AskUserQuestion, but the
//     branch is kept for future MCP-elicitation flows that ride
//     the same card.
//
//   - **Binary approve/reject** — everything else (policy ASK,
//     PermissionRequest for non-AskUserQuestion tools).
//
// Submit posts through `chatStore.submitApproval`, which:
//   1. optimistically flips the block to "responded" (instant UI),
//   2. calls `approve(targetSessionId, elicitationId, {action, content?})`
//      on `POST /v1/sessions/{id}/elicitations/{eid}/resolve`,
//   3. rolls back to "pending" on network error.

import { useContext } from "react";
import {
  CheckIcon,
  ClipboardListIcon,
  ClockIcon,
  ExternalLinkIcon,
  InfoIcon,
  MessageCircleQuestionMark,
  TerminalIcon,
  XIcon,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDescendantSession } from "@/hooks/useChildSessions";
import {
  type AskUserQuestionPayload,
  castAskUserQuestionPayload,
  exitPlanModePlan,
  parseAskUserQuestionPreview,
} from "@/lib/askUserQuestion";
import { isNativePolicyName, nativeCodingAgentForPolicyName } from "@/lib/nativeCodingAgents";
import { formatPreview } from "@/lib/previewFormat";
import type { RenderItem } from "@/lib/renderItems";
import { Link, useLocation } from "@/lib/routing";
import { sessionNavigationSearch } from "@/lib/sessionNavigation";
import type { AgyPermission, CodexPersistMode, RememberScope } from "@/lib/types";
import { cn } from "@/lib/utils";
import { childPrimaryLabel } from "@/shell/subagentLabel";
import { useChatStore } from "@/store/chatStore";
import {
  ConversationScopeContext,
  useScopedConversationId,
} from "@/components/chat/conversationScope";
import { AskUserQuestionForm, type AskUserQuestionAnswers } from "./AskUserQuestionForm";
import { ElicitationMessage } from "./ElicitationMessage";
import {
  type ElicitationAnswers,
  ElicitationSchemaForm,
  schemaFields,
} from "./ElicitationSchemaForm";
import { ExitPlanModeReview } from "./ExitPlanModeReview";

const AUTO_RESOLVED_DETAIL =
  "This request was answered outside this view, for example in the " +
  "agent's own terminal, another tab, or the approve page.";

/**
 * Extract the answer-option labels from an AskUserQuestion-shaped
 * ``requestedSchema``. Returns an empty array for any other schema.
 *
 * Currently unused for built-in AskUserQuestion (which routes
 * through PermissionRequest with a content_preview rather than a
 * structured schema), but kept for MCP-elicitation paths that may
 * emit this shape.
 */
function extractOptionLabels(schema: Record<string, unknown>): string[] {
  const properties = schema.properties;
  if (!properties || typeof properties !== "object") return [];
  // Only when ``answer`` is the whole question. A schema pairing it with, say,
  // a required ``reason`` renders as buttons that submit the answer and drop
  // the reason the server said it needed; that shape belongs to the form.
  if (Object.keys(properties as Record<string, unknown>).length !== 1) return [];
  const answer = (properties as Record<string, unknown>).answer;
  if (!answer || typeof answer !== "object") return [];
  const enumValues = (answer as Record<string, unknown>).enum;
  if (!Array.isArray(enumValues)) return [];
  return enumValues.filter((v): v is string => typeof v === "string" && v.length > 0);
}

/**
 * Verdict submitter — same signature as `chatStore.submitApproval`.
 * Injectable so surfaces outside the active chat (the Inbox page)
 * can route the verdict to the owning session themselves.
 */
export type SubmitApprovalFn = (
  elicitationId: string,
  action: "accept" | "decline",
  content?: Record<string, unknown>,
  meta?: Record<string, unknown>,
) => void;

/**
 * Copy for a surface outside the English session chat (the team chat).
 * Covers the binary card, its option buttons, the generic and option
 * verdicts and the plan verdict lines. The pending plan review, Codex
 * command details and question forms keep their own copy.
 */
export interface ApprovalCardLabels {
  /** Pending title of the binary card, e.g. "Freigabe nötig". */
  title: string;
  approve: string;
  reject: string;
  approved: string;
  rejected: string;
  cancelled: string;
  resolvedElsewhere: string;
  resolvedElsewhereDetail: string;
  /** Verdict plus resolver, e.g. `("Genehmigt", "Anna") => "Genehmigt von Anna"`. */
  byResolver: (verdict: string, resolver: string) => string;
  /** Option button: accept plus session-wide auto mode. */
  approveAutoMode: string;
  /** Tooltip of the auto-mode button. */
  approveAutoModeTitle: string;
  /** Option button: accept plus edit auto-accept. */
  acceptAllEdits: string;
  /** Option button: accept plus a session allow rule, e.g. `("Bash") => "…"`. */
  approveRemember: (target: string) => string;
  /** Tooltip of the remember button scoped to a WebFetch host. */
  rememberHostTitle: (host: string) => string;
  /** Tooltip of the remember button scoped to every call of a tool. */
  rememberToolTitle: (tool: string) => string;
  /** Codex persist option: this session only. */
  approveSession: string;
  /** Codex persist option: always. */
  alwaysAllow: string;
  /** Antigravity prefix options; the prefix renders as code between the parts. */
  allowPrefixSession: { before: string; after: string };
  allowPrefixAlways: { before: string; after: string };
  /** Codex command option that stores an exec-policy amendment. */
  approveAndRemember: string;
  approvedAndRemembered: string;
  approvedAutoMode: string;
  approvedAllEdits: string;
  /** Remember verdict; `target` is null when the scope is unknown. */
  approvedRemember: (target: string | null) => string;
  approvedSession: string;
  alwaysAllowed: string;
  sessionAllowRequested: string;
  alwaysAllowRequested: string;
  planApproved: string;
  planApprovedAutoMode: string;
  planRejected: string;
  promptExpired: string;
  promptExpiredHint: string;
}

/** Sub-agent session behind a prompt mirrored into an ancestor's view. */
export interface SubagentRequester {
  /** Sub-agent session that owns the prompt, e.g. ``"conv_child123"``. */
  sessionId: string;
  /** Session whose view shows the mirrored card, e.g. ``"conv_root"``. */
  ancestorSessionId: string;
}

/**
 * Names and links the sub-agent behind a mirrored prompt, so the card is not
 * read as a request from the session being viewed.
 */
function SubagentRequesterLine({ sessionId, ancestorSessionId }: SubagentRequester) {
  const child = useDescendantSession(ancestorSessionId, sessionId);
  const search = sessionNavigationSearch(useLocation().search);
  const label = (child && childPrimaryLabel(child)) || null;
  const link = (
    <Link
      to={{ pathname: `/c/${sessionId}`, search }}
      componentId="approval.open_subagent"
      className="font-medium text-foreground"
    >
      {label ?? "sub-agent"}
    </Link>
  );
  return (
    <span data-testid="approval-card-requester" className="text-sm">
      {label ? <>Requested by sub-agent {link}</> : <>Requested by a {link}</>}
    </span>
  );
}

interface ApprovalCardProps {
  elicitationId: string;
  message: string;
  phase: string;
  policyName: string;
  contentPreview: string;
  requestedSchema: Record<string, unknown>;
  /**
   * Standalone approval page URL when the elicitation uses URL mode.
   * When present, the pending card renders a link to the approval page
   * instead of inline approve/reject buttons.
   */
  url?: string | null;
  status: "pending" | "responded";
  response: {
    action: "accept" | "decline" | "cancel" | "auto_resolved";
    /** Why an `auto_resolved` card has no verdict; see `ElicitationBlock`. */
    reason?: "unanswered";
    content?: Record<string, unknown>;
    _meta?: Record<string, unknown>;
  } | null;
  /**
   * Structured AskUserQuestion payload — set when the server-side
   * PermissionRequest endpoint detected the gated tool is
   * AskUserQuestion. Carries the FULL question + options structure
   * (not truncated like `contentPreview`). Optional/null for
   * other elicitations.
   */
  askUserQuestion?: Record<string, unknown> | null;
  /**
   * Full ExitPlanMode tool_input (untruncated) — set when the
   * server-side PermissionRequest endpoint detected the gated tool
   * is ExitPlanMode. The card renders `plan` as markdown with
   * plan-review actions. Optional/null for other elicitations.
   */
  exitPlanMode?: Record<string, unknown> | null;
  /**
   * Structured Codex command approval details. When present, the
   * card renders command metadata instead of the raw JSON preview.
   */
  codexCommand?: {
    command: string;
    cwd: string | null;
    reason: string | null;
    execPolicyAmendment: string[] | null;
  } | null;
  /**
   * Claude-native edit-tool prompts only: when true, the binary
   * approve/reject card also offers an "Accept & allow all edits"
   * button. Accepting through it asks the server to switch the
   * session into Claude Code's ``acceptEdits`` mode (the web
   * equivalent of the native shift+tab toggle). Absent/false for
   * every other elicitation, so the button never renders where the
   * mode switch would be a no-op.
   */
  allowAllEdits?: boolean;
  /**
   * Eligible Claude-native tool prompts: when true, the card offers an
   * "Approve & switch to auto mode" button — accept plus a session-scoped
   * ``setMode(auto)``, so Claude reviews this session's later permissions
   * automatically. Absent/false where the switch was never offered.
   */
  allowAutoMode?: boolean;
  /**
   * Claude-native non-edit tool prompts only: when set, the binary
   * approve/reject card also offers an "Approve & don't ask again for
   * <host|tool>" button. Accepting through it asks the server to
   * install a session-scoped allow rule for the tool (scoped to
   * ``host`` for WebFetch, tool-wide otherwise) — the web equivalent
   * of Claude Code's native "don't ask again" permission option, so
   * same-scope calls stop re-prompting. Absent/null for every other
   * elicitation (edit tools take the ``allowAllEdits`` path instead).
   */
  rememberScope?: RememberScope | null;
  /** Codex-native MCP persistence scopes advertised by the request. */
  codexPersistModes?: CodexPersistMode[];
  /** Antigravity prompt details (see {@link AgyPermission}); null outside agy permission requests. */
  agyPermission?: AgyPermission | null;
  /**
   * Verdict submitter override. Defaults to `chatStore.submitApproval`
   * (the in-chat path: optimistic block flip + resolve POST + rollback).
   * The Inbox page passes its own handler because its cards belong to
   * sessions other than the chat store's active one.
   */
  onSubmit?: SubmitApprovalFn;
  /** Set when the prompt was mirrored up from a sub-agent session. */
  requester?: SubagentRequester | null;
  /** Extra classes for the card container, e.g. to drop its border when embedded. */
  className?: string;
  /** Surface copy; defaults to the English session-chat copy. */
  labels?: ApprovalCardLabels;
  /**
   * Who answered, when the surface knows it. Shown with an accept/decline
   * verdict, e.g. "Approved by Anna".
   */
  resolvedBy?: string | null;
  /**
   * Viewer may read but not answer: the pending card shows the request and
   * this note instead of any action control.
   */
  readOnlyNote?: string | null;
}

const EMPTY_CODEX_PERSIST_MODES: CodexPersistMode[] = [];

// Option buttons wrap instead of overflowing the card on narrow panes
// (WCAG 1.4.10 reflow at 320 px). `min-h-7` keeps a one-line button at the
// `sm` height, so wide layouts render exactly as before.
const OPTION_BUTTON_CLASS = "h-auto min-h-7 max-w-full py-0.5 whitespace-normal text-left";

export function ApprovalCard({
  elicitationId,
  message,
  phase,
  policyName,
  contentPreview,
  requestedSchema,
  url,
  status,
  response,
  askUserQuestion,
  exitPlanMode,
  codexCommand,
  allowAllEdits,
  allowAutoMode,
  rememberScope,
  codexPersistModes = EMPTY_CODEX_PERSIST_MODES,
  agyPermission,
  onSubmit,
  requester,
  className,
  labels,
  resolvedBy,
  readOnlyNote,
}: ApprovalCardProps) {
  // In a side-chat pane this resolves to the child id, so the verdict targets
  // the child's elicitation rather than the main conversation's. null (the main
  // transcript) leaves submitApproval on its active-conversation default.
  const scopedConversationId = useContext(ConversationScopeContext);
  const submit: SubmitApprovalFn =
    onSubmit ??
    ((id, action, content, meta) => {
      const store = useChatStore.getState();
      // Keep the exact call shape for the main chat (no scope); pass the child
      // id only when scoped so a side-chat verdict targets the child.
      if (scopedConversationId) {
        void store.submitApproval(id, action, content, meta, scopedConversationId);
      } else if (meta === undefined) {
        void store.submitApproval(id, action, content);
      } else {
        void store.submitApproval(id, action, content, meta);
      }
    });
  const submitBinary = (action: "accept" | "decline") => {
    submit(elicitationId, action);
  };
  const submitOption = (label: string) => {
    submit(elicitationId, "accept", { answer: label });
  };
  const submitSchemaAnswers = (content: ElicitationAnswers) => {
    submit(elicitationId, "accept", content);
  };
  const submitAnswers = (answers: AskUserQuestionAnswers) => {
    // ``content`` is MCP's ``ElicitResult.content``: a flat
    // ``{[field]: scalar | string[]}`` map, where each AskUserQuestion
    // question text is one "field". Wrapping in a nested ``answers``
    // object would fail server-side schema validation (content values
    // must be str / int / float / bool / list[str] / null).
    submit(elicitationId, "accept", answers);
  };
  const submitExecPolicyAmendment = (amendment: string[]) => {
    submit(elicitationId, "accept", { execpolicy_amendment: amendment });
  };
  const submitAutoMode = () => {
    submit(elicitationId, "accept", { allow_auto_mode: true });
  };
  const submitAllowAllEdits = () => {
    // Accept AND ask the server to switch the session's permission
    // mode. The server reads ``content.allow_all_edits`` and echoes a
    // ``setMode`` permission update back to the PermissionRequest
    // hook: ``acceptEdits`` for edit-tool prompts, ``auto`` when the
    // gated tool is ExitPlanMode (the plan card's "Yes, and use auto
    // mode" action — same flag, server picks the mode).
    submit(elicitationId, "accept", { allow_all_edits: true });
  };
  const submitRemember = () => {
    // Accept AND ask the server to install a session-scoped allow rule
    // so the same scope stops prompting. The server reads
    // ``content.remember`` and re-derives the rule scope (WebFetch
    // domain or tool-wide) from the gated tool itself — the client only
    // signals intent, never the rule — then echoes an ``addRules``
    // permission update back to the PermissionRequest hook.
    submit(elicitationId, "accept", { remember: true });
  };
  // Codex MCP and agy persist accepts share the `_meta.persist` verdict shape.
  const submitPersist = (mode: CodexPersistMode) => {
    submit(elicitationId, "accept", undefined, { persist: mode });
  };
  const submitPlanRejection = (feedback: string) => {
    // The typed feedback rides on `content.feedback`; the server
    // forwards it to Claude as the deny `message`, so Claude stays in
    // plan mode and revises toward it. Empty feedback → plain decline.
    const trimmed = feedback.trim();
    submit(elicitationId, "decline", trimmed ? { feedback: trimmed } : undefined);
  };

  // Mode detection. Prefer the server-stamped structured payload
  // (full, non-truncated); fall back to parsing the content_preview
  // JSON for backwards compatibility with elicitations published
  // before the structured field was added.
  const askPayload: AskUserQuestionPayload | null =
    castAskUserQuestionPayload(askUserQuestion) ?? parseAskUserQuestionPreview(contentPreview);
  // ExitPlanMode plan review: the server stamps the full tool_input
  // as `exit_plan_mode`; a usable plan card needs the `plan` markdown
  // string. Anything else falls back to the binary card.
  const planMarkdown = exitPlanModePlan(exitPlanMode);
  const isExitPlanMode = planMarkdown !== null;
  const optionLabels = askPayload === null ? extractOptionLabels(requestedSchema) : [];
  const isAskUserQuestion = askPayload !== null;
  const isMultiChoice = optionLabels.length > 0;
  // Any other schema that names fields: a free-form string, several
  // properties, an enum under a name other than ``answer``. These used to
  // fall through to Approve / Reject, which cannot answer them.
  const schemaFormFields =
    askPayload === null && !isMultiChoice ? schemaFields(requestedSchema) : [];
  const isSchemaForm = schemaFormFields.length > 0;
  const isCodexCommandApproval = codexCommand !== null && codexCommand !== undefined;
  // External URL: the elicitation points to a third-party page (OAuth,
  // external MCP server, etc.) — show a link. Our own /approve/...
  // paths are handled inline with approve/reject buttons.
  const isExternalUrl = typeof url === "string" && url.length > 0 && !url.startsWith("/approve/");
  // What the header's "· <tag>" slot shows. Native bridges stamp a synthetic
  // `policy_name` (provenance, not a policy anyone wrote), so name the product
  // that asked — and show nothing when the stamp names no vendor. A real policy
  // name renders verbatim so users can tell which of their policies asked.
  const isNativePolicy = isNativePolicyName(policyName);
  const policyLabel = isNativePolicy
    ? (nativeCodingAgentForPolicyName(policyName)?.displayName ?? "")
    : policyName;
  // Native prompts also stamp a constant, internal-sounding phase
  // ("pre_tool_use", "codex_command_approval", ...). It carries no
  // information the card doesn't already show, so hide it for them.
  const showPhase = phase !== "" && !isNativePolicy;
  const askUserQuestionTitle =
    policyName.startsWith("agy_") || phase.startsWith("agy_")
      ? "Antigravity needs your input"
      : policyName.startsWith("codex_") || phase.startsWith("codex_")
        ? "Codex needs input"
        : policyName.startsWith("cursor_") || phase.startsWith("cursor_")
          ? "Cursor has questions"
          : "Claude has questions";

  // Hide the raw JSON preview for AskUserQuestion (the form already
  // renders the questions + options structurally) and for option-
  // button mode (the buttons render the choices). Codex command
  // approvals get a dedicated command render below, so showing the
  // transport JSON would expose unrelated ids and duplicate details.
  const formattedPreview =
    isAskUserQuestion || isExitPlanMode || isMultiChoice || isSchemaForm || isCodexCommandApproval
      ? ""
      : formatPreview(contentPreview);
  const execPolicyAmendment =
    codexCommand?.execPolicyAmendment && codexCommand.execPolicyAmendment.length > 0
      ? codexCommand.execPolicyAmendment
      : null;
  const acceptedWithExecPolicy =
    Array.isArray(response?.content?.execpolicy_amendment) &&
    response.content.execpolicy_amendment.every((entry) => typeof entry === "string");
  const acceptedAllEdits = response?.content?.allow_all_edits === true;
  const acceptedAutoMode =
    response?.action === "accept" && response.content?.allow_auto_mode === true;
  const acceptedRemember = response?.content?.remember === true;
  const acceptedPersist = response?.["_meta"]?.persist;
  // Persistent "don't ask again" affordance: label by the WebFetch
  // domain when present, else the tool name. Drives the third binary
  // button and the responded-state pill.
  const rememberTarget = rememberScope ? (rememberScope.host ?? rememberScope.tool) : null;
  // Tooltip spelling out the scope — the tool-wide case (no host) is a
  // broad grant (every call to the tool), so make that explicit rather
  // than letting the short button label imply a narrower scope.
  const rememberTitle = rememberScope
    ? rememberScope.host
      ? (labels?.rememberHostTitle(rememberScope.host) ??
        `Won't ask again for ${rememberScope.host} for the rest of this session`)
      : (labels?.rememberToolTitle(rememberScope.tool) ??
        `Won't ask again for any ${rememberScope.tool} call for the rest of this session`)
    : undefined;
  const binaryButtons = (
    <div className="flex flex-wrap gap-2 pt-1">
      <Button
        size="sm"
        className={OPTION_BUTTON_CLASS}
        onClick={() => submitBinary("accept")}
        componentId="approval.approve"
      >
        <CheckIcon className="mr-1 size-3.5 shrink-0" />
        <span className="min-w-0">{labels?.approve ?? "Approve"}</span>
      </Button>
      {agyPermission?.alwaysAllowPattern && (
        <>
          <Button
            size="sm"
            variant="outline"
            className={OPTION_BUTTON_CLASS}
            onClick={() => submitPersist("session")}
            title={`Ask Antigravity to allow commands starting with “${agyPermission.alwaysAllowPattern}” for this conversation`}
            data-testid="approval-card-agy-session-allow"
            componentId="approval.approve_session_agy"
          >
            <CheckIcon className="mr-1 size-3.5 shrink-0" />
            <span className="min-w-0">
              {labels?.allowPrefixSession.before ?? "Allow commands starting with"}{" "}
              <code className="break-all rounded bg-muted px-1 font-mono">
                {agyPermission.alwaysAllowPattern}
              </code>{" "}
              {labels?.allowPrefixSession.after ?? "for this session"}
            </span>
          </Button>
          <Button
            size="sm"
            variant="outline"
            className={OPTION_BUTTON_CLASS}
            onClick={() => submitPersist("always")}
            title={`Ask Antigravity to save a rule allowing commands starting with “${agyPermission.alwaysAllowPattern}” for this Omnigent session`}
            data-testid="approval-card-agy-always-allow"
            componentId="approval.approve_always_agy"
          >
            <CheckIcon className="mr-1 size-3.5 shrink-0" />
            <span className="min-w-0">
              {labels?.allowPrefixAlways.before ?? "Always allow commands starting with"}{" "}
              <code className="break-all rounded bg-muted px-1 font-mono">
                {agyPermission.alwaysAllowPattern}
              </code>
              {labels?.allowPrefixAlways.after ? <> {labels.allowPrefixAlways.after}</> : null}
            </span>
          </Button>
        </>
      )}
      {codexPersistModes.includes("session") && (
        <Button
          size="sm"
          variant="outline"
          className={OPTION_BUTTON_CLASS}
          onClick={() => submitPersist("session")}
          componentId="approval.approve_session"
        >
          <CheckIcon className="mr-1 size-3.5 shrink-0" />
          <span className="min-w-0">{labels?.approveSession ?? "Approve for this session"}</span>
        </Button>
      )}
      {codexPersistModes.includes("always") && (
        <Button
          size="sm"
          variant="outline"
          className={OPTION_BUTTON_CLASS}
          onClick={() => submitPersist("always")}
          componentId="approval.approve_always"
        >
          <CheckIcon className="mr-1 size-3.5 shrink-0" />
          <span className="min-w-0">{labels?.alwaysAllow ?? "Always allow"}</span>
        </Button>
      )}
      {allowAutoMode && (
        <Button
          size="sm"
          variant="outline"
          className={OPTION_BUTTON_CLASS}
          onClick={submitAutoMode}
          title={
            labels?.approveAutoModeTitle ??
            "Approve this request and let Claude review future tool permissions automatically for this session"
          }
          componentId="approval.approve_auto_mode"
        >
          <CheckIcon className="mr-1 size-3.5 shrink-0" />
          <span className="min-w-0">
            {labels?.approveAutoMode ?? "Approve & switch to auto mode"}
          </span>
        </Button>
      )}
      {allowAllEdits && (
        <Button
          size="sm"
          variant="outline"
          className={OPTION_BUTTON_CLASS}
          onClick={submitAllowAllEdits}
          componentId="approval.approve_allow_edits"
        >
          <CheckIcon className="mr-1 size-3.5 shrink-0" />
          <span className="min-w-0">{labels?.acceptAllEdits ?? "Accept & allow all edits"}</span>
        </Button>
      )}
      {rememberTarget && (
        <Button
          size="sm"
          variant="outline"
          className={OPTION_BUTTON_CLASS}
          onClick={submitRemember}
          title={rememberTitle}
          data-testid="approval-card-remember"
          componentId="approval.approve_remember"
        >
          <CheckIcon className="mr-1 size-3.5 shrink-0" />
          <span className="min-w-0 break-words">
            {labels?.approveRemember(rememberTarget) ??
              `Approve & don't ask again for ${rememberTarget}`}
          </span>
        </Button>
      )}
      <Button
        size="sm"
        variant="outline"
        className={OPTION_BUTTON_CLASS}
        onClick={() => submitBinary("decline")}
        componentId="approval.reject"
      >
        <XIcon className="mr-1 size-3.5 shrink-0" />
        <span className="min-w-0">{labels?.reject ?? "Reject"}</span>
      </Button>
    </div>
  );
  const codexCommandButtons = (
    <div className="flex flex-wrap items-center gap-2 pt-1" data-testid="codex-command-actions">
      <Button
        size="sm"
        className={OPTION_BUTTON_CLASS}
        onClick={() => submitBinary("accept")}
        componentId="approval.approve_command"
      >
        <CheckIcon className="mr-1 size-3.5 shrink-0" />
        <span className="min-w-0">{labels?.approve ?? "Approve"}</span>
      </Button>
      {execPolicyAmendment && (
        <Button
          size="sm"
          variant="outline"
          className={OPTION_BUTTON_CLASS}
          onClick={() => submitExecPolicyAmendment(execPolicyAmendment)}
          componentId="approval.approve_command_remember"
        >
          <CheckIcon className="mr-1 size-3.5 shrink-0" />
          <span className="min-w-0">{labels?.approveAndRemember ?? "Approve and remember"}</span>
        </Button>
      )}
      <Button
        size="sm"
        variant="outline"
        className={OPTION_BUTTON_CLASS}
        onClick={() => submitBinary("decline")}
      >
        <XIcon className="mr-1 size-3.5 shrink-0" />
        <span className="min-w-0">{labels?.reject ?? "Reject"}</span>
      </Button>
    </div>
  );

  const requesterLine = requester ? <SubagentRequesterLine {...requester} /> : null;

  if (status === "responded" && response) {
    const autoResolved = response.action === "auto_resolved";
    const promptExpired = autoResolved && response.reason === "unanswered";
    const accepted = response.action === "accept";

    // Distinguish three responded sub-states:
    //   1. AskUserQuestion submitted → "Submitted" + summary
    //   2. Single option chosen → "Selected: <label>"
    //   3. Auto-resolved / generic accept / decline
    //
    // For AskUserQuestion the ``content`` IS the answers map
    // (flat ``{[question]: answer}``) — matches MCP's
    // ElicitResult.content shape.
    const submittedAnswers =
      isAskUserQuestion && response.content && Object.keys(response.content).length > 0
        ? response.content
        : null;
    const selectedAnswer =
      !isAskUserQuestion && response.content && typeof response.content.answer === "string"
        ? (response.content.answer as string)
        : null;
    // Plan rejections can carry the feedback the user typed into the
    // card; echo it on the responded pill so the chat shows WHY the
    // plan went back for revision.
    const planRejectionFeedback =
      isExitPlanMode &&
      response.action === "decline" &&
      typeof response.content?.feedback === "string" &&
      response.content.feedback
        ? response.content.feedback
        : null;

    let icon = <XIcon className="size-4 text-destructive" />;
    let label = isExitPlanMode
      ? (labels?.planRejected ?? "Plan rejected")
      : (labels?.rejected ?? "Rejected");
    if (promptExpired) {
      // The server cleared the prompt because the hook stopped waiting
      // before anyone answered (a severed poll never re-parked, the ask
      // timed out). Nothing was decided, so say so and tell the user how
      // to get the agent moving again instead of implying an answer.
      icon = <ClockIcon className="size-4 text-muted-foreground" />;
      label = labels?.promptExpired ?? "Prompt expired";
    } else if (autoResolved) {
      // Verdict unknown here; explain the neutral pill.
      const detail = labels?.resolvedElsewhereDetail ?? AUTO_RESOLVED_DETAIL;
      icon = (
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="inline-flex" aria-label={detail}>
              <InfoIcon className="size-4 text-muted-foreground" />
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">{detail}</TooltipContent>
        </Tooltip>
      );
      label = labels?.resolvedElsewhere ?? "Resolved elsewhere";
    } else if (response.action === "cancel") {
      // Dismissed without deciding — neither approved nor rejected.
      icon = <InfoIcon className="size-4 text-muted-foreground" />;
      label = labels?.cancelled ?? "Cancelled";
    } else if (submittedAnswers !== null) {
      icon = <CheckIcon className="size-4 text-success" />;
      label = "Submitted";
    } else if (selectedAnswer !== null) {
      icon = <CheckIcon className="size-4 text-success" />;
      label = `Selected: ${selectedAnswer}`;
    } else if (acceptedWithExecPolicy) {
      icon = <CheckIcon className="size-4 text-success" />;
      label = labels?.approvedAndRemembered ?? "Approved and remembered";
    } else if (acceptedAutoMode) {
      icon = <CheckIcon className="size-4 text-success" />;
      label = labels?.approvedAutoMode ?? "Approved · auto mode";
    } else if (acceptedAllEdits) {
      icon = <CheckIcon className="size-4 text-success" />;
      label = isExitPlanMode
        ? (labels?.planApprovedAutoMode ?? "Plan approved · auto mode")
        : (labels?.approvedAllEdits ?? "Approved · auto-accepting edits");
    } else if (acceptedRemember) {
      icon = <CheckIcon className="size-4 text-success" />;
      label = labels
        ? labels.approvedRemember(rememberTarget)
        : rememberTarget
          ? `Approved · won't ask again for ${rememberTarget}`
          : "Approved · won't ask again";
    } else if (
      phase === "agy_permission" &&
      (acceptedPersist === "session" || acceptedPersist === "always")
    ) {
      icon = <InfoIcon className="size-4 text-muted-foreground" />;
      label =
        acceptedPersist === "session"
          ? (labels?.sessionAllowRequested ?? "Session allow requested")
          : (labels?.alwaysAllowRequested ?? "Always allow requested");
    } else if (acceptedPersist === "session") {
      icon = <CheckIcon className="size-4 text-success" />;
      label = labels?.approvedSession ?? "Approved for this session";
    } else if (acceptedPersist === "always") {
      icon = <CheckIcon className="size-4 text-success" />;
      label = labels?.alwaysAllowed ?? "Always allowed";
    } else if (accepted) {
      icon = <CheckIcon className="size-4 text-success" />;
      label = isExitPlanMode
        ? (labels?.planApproved ?? "Plan approved")
        : (labels?.approved ?? "Approved");
    }
    const decided = response.action === "accept" || response.action === "decline";
    if (decided && resolvedBy) {
      label = labels ? labels.byResolver(label, resolvedBy) : `${label} by ${resolvedBy}`;
    }

    // The gating message ("Claude wants to call **AskUserQuestion**") is
    // written in pending tense. On a question or plan card the ask has
    // already happened and the user answered it, so echoing it reads as
    // if the prompt were still outstanding — the answers below and the
    // verdict label already say what happened. The pending card omits it
    // for the same reason: purposeful content instead of the raw ask.
    const showGatingMessage = !isCodexCommandApproval && !isAskUserQuestion && !isExitPlanMode;
    const hasBody =
      requesterLine !== null ||
      showGatingMessage ||
      isCodexCommandApproval ||
      submittedAnswers !== null ||
      planRejectionFeedback !== null ||
      promptExpired;

    return (
      <Alert
        data-testid="approval-card"
        data-state="responded"
        className={cn("flex flex-col gap-1 border-muted", className)}
      >
        <AlertTitle className="flex min-w-0 items-center gap-2 text-ui">
          {icon}
          {/* NFR-032: a long resolver (raw email / user id) wraps inside the
              card instead of overflowing it; the full label stays readable. */}
          <span className="min-w-0 break-words" title={label} aria-label={label}>
            {label}
          </span>
          {policyLabel && <span className="text-muted-foreground text-sm">· {policyLabel}</span>}
        </AlertTitle>
        {hasBody && (
          <AlertDescription className="flex flex-col gap-1 text-sm">
            {requesterLine}
            {isCodexCommandApproval ? (
              <>
                {codexCommand.reason && <span>{codexCommand.reason}</span>}
                <pre className="overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-sm whitespace-pre-wrap">
                  {codexCommand.command}
                </pre>
                {codexCommand.cwd && (
                  <span>
                    <span className="text-muted-foreground">cwd: </span>
                    <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm">
                      {codexCommand.cwd}
                    </code>
                  </span>
                )}
              </>
            ) : showGatingMessage ? (
              <>
                <ElicitationMessage message={message} />
                {agyPermission?.actionDescription && (
                  <span
                    className="text-sm text-muted-foreground"
                    data-testid="agy-action-description"
                  >
                    {agyPermission.actionDescription}
                  </span>
                )}
              </>
            ) : null}
            {submittedAnswers !== null && (
              <ul className="flex flex-col gap-0.5">
                {Object.entries(submittedAnswers).map(([q, ans]) => (
                  <li key={q} className="flex flex-wrap items-baseline gap-x-1.5">
                    <span className="text-muted-foreground">{q}</span>
                    <span className="font-medium">
                      {Array.isArray(ans) ? ans.join(", ") : String(ans)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {planRejectionFeedback !== null && (
              <span className="italic" data-testid="plan-rejection-feedback">
                “{planRejectionFeedback}”
              </span>
            )}
            {promptExpired && (
              <span className="text-muted-foreground" data-testid="prompt-expired-hint">
                {labels?.promptExpiredHint ??
                  "Nobody answered before the agent stopped waiting. Send a message to continue."}
              </span>
            )}
          </AlertDescription>
        )}
      </Alert>
    );
  }

  // Pending state.
  return (
    <Alert
      data-testid="approval-card"
      data-state="pending"
      className={cn("flex flex-col gap-2 py-3 px-4", className)}
    >
      <AlertTitle className="flex items-center gap-2 text-ui">
        {isCodexCommandApproval ? (
          <TerminalIcon className="size-4 text-yellow-600 dark:text-yellow-400" />
        ) : isExitPlanMode ? (
          <ClipboardListIcon className="size-4 text-yellow-600 dark:text-yellow-400" />
        ) : (
          <MessageCircleQuestionMark className="size-4 text-yellow-600 dark:text-yellow-400" />
        )}
        {isCodexCommandApproval
          ? "Command approval"
          : isExitPlanMode
            ? "Plan review"
            : isAskUserQuestion
              ? askUserQuestionTitle
              : isMultiChoice
                ? "Choose an option"
                : (labels?.title ?? "Approval required")}
        {policyLabel && !isAskUserQuestion && !isExitPlanMode && (
          <span className="text-muted-foreground text-sm">· {policyLabel}</span>
        )}
        {showPhase && !isMultiChoice && !isAskUserQuestion && !isExitPlanMode && (
          <span className="text-muted-foreground text-sm">({phase})</span>
        )}
      </AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        {requesterLine}
        {readOnlyNote ? (
          <>
            <ElicitationMessage message={message} />
            {formattedPreview && (
              <pre className="max-h-64 overflow-y-auto rounded bg-muted px-2 py-1 font-mono text-sm whitespace-pre-wrap break-words">
                {formattedPreview}
              </pre>
            )}
            <span data-testid="approval-card-read-only" className="text-sm text-muted-foreground">
              {readOnlyNote}
            </span>
          </>
        ) : isExitPlanMode ? (
          <>
            <span>Claude finished planning and wants to proceed.</span>
            <ExitPlanModeReview
              plan={planMarkdown}
              onAcceptAuto={submitAllowAllEdits}
              onAccept={() => submitBinary("accept")}
              onReject={submitPlanRejection}
            />
          </>
        ) : isAskUserQuestion ? (
          <AskUserQuestionForm
            questions={askPayload.questions}
            onSubmit={submitAnswers}
            onReject={() => submitBinary("decline")}
          />
        ) : isCodexCommandApproval ? (
          <>
            <span>Codex wants to run this command.</span>
            {codexCommand.reason && <span className="text-foreground">{codexCommand.reason}</span>}
            <pre className="overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-sm text-foreground whitespace-pre-wrap">
              {codexCommand.command}
            </pre>
            {codexCommand.cwd && (
              <span className="text-sm">
                cwd:{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono">{codexCommand.cwd}</code>
              </span>
            )}
            {codexCommandButtons}
          </>
        ) : (
          <>
            <ElicitationMessage message={message} />
            {agyPermission?.actionDescription && (
              <span className="text-sm text-muted-foreground" data-testid="agy-action-description">
                {agyPermission.actionDescription}
              </span>
            )}
            {agyPermission?.alwaysAllowPattern && (
              <span className="text-sm text-muted-foreground">
                Always allow saves a rule for this Omnigent session. New sessions have separate
                rules.
              </span>
            )}
            {formattedPreview && (
              <pre className="max-h-64 overflow-y-auto rounded bg-muted px-2 py-1 font-mono text-sm whitespace-pre-wrap break-words">
                {formattedPreview}
              </pre>
            )}
            {isExternalUrl ? (
              <div className="flex flex-wrap gap-2 pt-1">
                <Button size="sm" asChild>
                  <a href={url!} target="_blank" rel="noopener noreferrer">
                    <ExternalLinkIcon className="mr-1 size-3.5" />
                    Open approval page
                  </a>
                </Button>
              </div>
            ) : isSchemaForm ? (
              <ElicitationSchemaForm
                fields={schemaFormFields}
                onSubmit={submitSchemaAnswers}
                onReject={() => submitBinary("decline")}
              />
            ) : isMultiChoice ? (
              <div className="flex flex-wrap gap-2 pt-1" data-testid="approval-card-options">
                {optionLabels.map((optLabel) => (
                  <Button
                    key={optLabel}
                    size="sm"
                    variant="outline"
                    onClick={() => submitOption(optLabel)}
                  >
                    {optLabel}
                  </Button>
                ))}
              </div>
            ) : (
              binaryButtons
            )}
          </>
        )}
      </AlertDescription>
    </Alert>
  );
}

/**
 * Render an elicitation ``RenderItem`` as an ``ApprovalCard``. The prop
 * mapping lives here, in one place, so the two callers stay in sync:
 * ``BlockRenderer`` (inline in the message stream) and ``ChatPage``'s
 * pinned tray (pending cards lifted above the composer). Pass ``onSubmit``
 * to route the verdict somewhere other than the active chat store.
 */
export function ElicitationCard({
  item,
  onSubmit,
  labels,
  resolvedBy,
  readOnlyNote,
  className,
}: {
  item: Extract<RenderItem, { kind: "elicitation" }>;
  onSubmit?: SubmitApprovalFn;
} & Pick<ApprovalCardProps, "labels" | "resolvedBy" | "readOnlyNote" | "className">) {
  // A sub-agent's prompt is mirrored into its ancestors' streams with
  // ``targetSessionId`` naming the sub-agent that asked.
  const conversationId = useScopedConversationId();
  const requester =
    item.targetSessionId && conversationId && item.targetSessionId !== conversationId
      ? { sessionId: item.targetSessionId, ancestorSessionId: conversationId }
      : null;
  return (
    <ApprovalCard
      elicitationId={item.elicitationId}
      message={item.message}
      phase={item.phase}
      policyName={item.policyName}
      contentPreview={item.contentPreview}
      requestedSchema={item.requestedSchema}
      url={item.url}
      status={item.status}
      response={item.response}
      askUserQuestion={item.askUserQuestion}
      exitPlanMode={item.exitPlanMode}
      codexCommand={item.codexCommand}
      allowAllEdits={item.allowAllEdits}
      allowAutoMode={item.allowAutoMode}
      rememberScope={item.rememberScope}
      codexPersistModes={item.codexPersistModes}
      agyPermission={item.agyPermission}
      onSubmit={onSubmit}
      requester={requester}
      labels={labels}
      resolvedBy={resolvedBy}
      readOnlyNote={readOnlyNote}
      className={className}
    />
  );
}
