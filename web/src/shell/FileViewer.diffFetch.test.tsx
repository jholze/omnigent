// Opening a changed image, PDF, 3D model or binary file must not request its diff:
// the viewer never renders one for those types. The real useFileDiff runs against
// a stubbed fetch so the assertion holds whichever layer gates the query.

import { cleanup, render, waitFor } from "@testing-library/react";
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

function openChangedFile(file: OpenedFile | { path: string; loading: true }): QueryClient {
  vi.mocked(useFileContent).mockReturnValue(
    ("loading" in file
      ? { data: undefined }
      : {
          data: {
            object: "session.environment.filesystem.file_content",
            path: file.path,
            content_type: file.content_type,
            encoding: file.encoding,
            content: "AAAA",
            bytes: 4,
          },
        }) as unknown as ReturnType<typeof useFileContent>,
  );
  vi.mocked(useWorkspaceChangedFiles).mockReturnValue({
    data: {
      available: true,
      data: [{ path: file.path, name: file.path, status: "created", bytes: 4, modified_at: null }],
    },
  } as unknown as ReturnType<typeof useWorkspaceChangedFiles>);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <FileViewer open conversationId="conv_1" path={file.path} onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
}

// The viewer dispatches an enabled diff query synchronously on mount, so once
// no query is in flight the recorded requests are final — no fixed-delay window
// that a slow machine could outrun.
async function diffRequests(queryClient: QueryClient): Promise<string[]> {
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));
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
    // Binary content typed only by its base64 encoding, with no extension the
    // classifier recognizes — the case the metadata gate must still suppress.
    { kind: "encoding-only binary", path: "datablob", content_type: null, encoding: "base64" },
  ];

  it.each(neverDiffed)("does not request a diff for a changed $kind file", async (file) => {
    expect(await diffRequests(openChangedFile(file))).toEqual([]);
  });

  it("does not request a diff while a changed file's metadata is still loading", async () => {
    // A text-like extension would classify as diffable, but the file could still
    // resolve to media/binary content; wait for the metadata before fetching.
    expect(await diffRequests(openChangedFile({ path: "notes.txt", loading: true }))).toEqual([]);
  });

  it("still requests the diff for a changed text file", async () => {
    const queryClient = openChangedFile({
      kind: "text",
      path: "notes.txt",
      content_type: "text/plain",
      encoding: "utf-8",
    });
    expect(await diffRequests(queryClient)).toEqual([
      "/v1/sessions/conv_1/resources/environments/default/diff/notes.txt",
    ]);
  });
});
