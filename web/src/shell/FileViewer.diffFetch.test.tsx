// Opening a changed image, PDF, 3D model or binary file must not request its diff:
// the viewer never renders one for those types. The real useFileDiff runs against
// a stubbed fetch so the assertion holds whichever layer gates the query.

import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./CodeViewer", () => ({
  CodeViewer: ({ path }: { path: string }) => <div data-testid="code-viewer">{path}</div>,
}));

vi.mock("./CommentsPanel", () => ({
  CommentsPanel: () => <div data-testid="comments-panel" />,
}));

vi.mock("./MonacoDiffViewer", () => ({
  MonacoDiffViewer: () => <div data-testid="diff-viewer" />,
}));

vi.mock("@/hooks/useIsMobileViewport", () => ({
  useIsMobileViewport: () => false,
}));

vi.mock("@/hooks/useComments", () => ({
  useComments: () => ({ data: [] }),
  useAddComment: () => ({ mutate: vi.fn() }),
  useUpdateComment: () => ({ mutate: vi.fn() }),
  useDeleteComment: () => ({ mutate: vi.fn() }),
}));

vi.mock("@/hooks/useFileContent", () => ({
  useFileContent: vi.fn(),
  downloadWorkspaceFile: vi.fn(),
}));

vi.mock("@/hooks/useWorkspaceChangedFiles", () => ({
  useWorkspaceChangedFiles: vi.fn(),
  useWorkspaceServeable: () => true,
}));

vi.mock("@/hooks/useResizablePanel", () => ({
  useResizablePanel: () => ({
    panelWidth: 400,
    handleProps: {
      onMouseDown: vi.fn(),
      onKeyDown: vi.fn(),
      role: "separator" as const,
      "aria-orientation": "vertical" as const,
      "aria-label": "Resize panel",
      tabIndex: 0,
    },
    isDesktop: true,
  }),
}));

vi.mock("@/hooks/CommentSenderContext", () => ({
  CommentSenderProvider: ({ children }: { children: React.ReactNode }) => children,
  useOptionalCommentSender: () => null,
}));

vi.mock("@/store/chatStore", () => ({
  useChatStore: (selector: (s: { boundAgentId: null; status: string }) => unknown) =>
    selector({ boundAgentId: null, status: "idle" }),
}));

import { useFileContent } from "@/hooks/useFileContent";
import { useWorkspaceChangedFiles } from "@/hooks/useWorkspaceChangedFiles";
import { FileViewer } from "./FileViewer";

const DIFF_URL_MARKER = "/resources/environments/default/diff/";

const fetchMock = vi.fn<typeof fetch>(
  async () =>
    ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        object: "session.environment.filesystem.file_diff",
        path: "x",
        before: null,
        after: "",
      }),
    }) as unknown as Response,
);

interface OpenedFile {
  kind: string;
  path: string;
  content_type: string | null;
  encoding: "utf-8" | "base64";
}

function openChangedFile({ path, content_type, encoding }: OpenedFile) {
  vi.mocked(useFileContent).mockReturnValue({
    data: {
      object: "session.environment.filesystem.file_content",
      path,
      content_type,
      encoding,
      content: "AAAA",
      bytes: 4,
    },
  } as unknown as ReturnType<typeof useFileContent>);
  vi.mocked(useWorkspaceChangedFiles).mockReturnValue({
    data: {
      available: true,
      data: [{ path, name: path, status: "created", bytes: 4, modified_at: null }],
    },
  } as unknown as ReturnType<typeof useWorkspaceChangedFiles>);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <FileViewer open conversationId="conv_1" path={path} onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function settleQueries(): Promise<string[]> {
  await screen.findByTestId("code-viewer");
  // Give a wrongly-enabled diff query time to issue its request.
  await act(
    () =>
      new Promise<void>((resolve) => {
        setTimeout(resolve, 50);
      }),
  );
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((url) => url.includes(DIFF_URL_MARKER));
}

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FileViewer — diff fetch for files it never diffs", () => {
  const neverDiffed: OpenedFile[] = [
    { kind: "image", path: "report.png", content_type: "image/png", encoding: "base64" },
    { kind: "PDF", path: "sample.pdf", content_type: "application/pdf", encoding: "base64" },
    {
      kind: "3D model",
      path: "part.stl",
      content_type: "application/vnd.ms-pki.stl",
      encoding: "utf-8",
    },
    { kind: "video", path: "recording.mp4", content_type: "video/mp4", encoding: "base64" },
    { kind: "binary", path: "bundle.zip", content_type: "application/zip", encoding: "base64" },
  ];

  it.each(neverDiffed)("does not request a diff for a changed $kind file", async (file) => {
    openChangedFile(file);
    expect(await settleQueries()).toEqual([]);
  });

  it("still requests the diff for a changed text file", async () => {
    openChangedFile({
      kind: "text",
      path: "notes.txt",
      content_type: "text/plain",
      encoding: "utf-8",
    });
    expect(await settleQueries()).toEqual([
      "/v1/sessions/conv_1/resources/environments/default/diff/notes.txt",
    ]);
  });
});
