import { useEffect, useRef, useState } from "react";
import {
  getArtifacts,
  getRoots,
  readArtifact,
  type ArtifactContent,
  type ArtifactInfo,
  type RootInfo,
} from "../api";

function kindFromPath(path: string): string {
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "image";
  if (["html", "htm"].includes(ext)) return "html";
  if (ext === "md") return "markdown";
  if (ext === "csv") return "csv";
  if (ext === "pdf") return "pdf";
  if (["py", "js", "ts", "tsx", "jsx", "json", "sh", "css"].includes(ext)) return "code";
  return "text";
}

function unavailableContent(
  artifact: ArtifactInfo,
  err: unknown,
  fallback: string,
): ArtifactContent {
  const message = err instanceof Error && err.message ? err.message : fallback;
  return {
    ok: false,
    source: artifact.source,
    error: message,
    path: artifact.path,
    kind: artifact.kind || kindFromPath(artifact.path),
    preview_status: "unavailable",
    download_status: "unavailable",
  };
}

function readKey(sessionId: string, artifact: ArtifactInfo | null): string | null {
  if (!artifact) return null;
  return JSON.stringify([
    sessionId,
    artifact.origin || "artifacts",
    artifact.source || "local",
    artifact.id || "",
    artifact.path,
    artifact.name,
    artifact.kind,
    artifact.size,
    artifact.modified_at,
    artifact.preview_status || "",
    artifact.download_status || "",
  ]);
}

interface Options {
  active: boolean;
  sessionId: string;
  refreshKey: number;
  showArtifacts: boolean;
  unavailableMessage: string;
  artifactOpenRequest?: { path: string; nonce: number } | null;
  onArtifactOpenConsumed?: () => void;
}

export function useArtifactBrowser({
  active,
  sessionId,
  refreshKey,
  showArtifacts,
  unavailableMessage,
  artifactOpenRequest,
  onArtifactOpenConsumed,
}: Options) {
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  const [rootDirs, setRootDirs] = useState<RootInfo[]>([]);
  const [selected, setSelected] = useState<ArtifactInfo | null>(null);
  const [loadedContent, setLoadedContent] = useState<{
    key: string;
    value: ArtifactContent;
  } | null>(null);
  const currentSessionId = useRef(sessionId);
  currentSessionId.current = sessionId;
  const artifactListSeq = useRef(0);
  const rootListSeq = useRef(0);
  const artifactReadSeq = useRef(0);
  const mounted = useRef(true);
  const selectedReadKey = readKey(sessionId, selected);
  const content =
    selectedReadKey && loadedContent?.key === selectedReadKey ? loadedContent.value : null;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      artifactReadSeq.current += 1;
    };
  }, []);

  const refreshArtifacts = () => {
    const requestSessionId = sessionId;
    const seq = ++artifactListSeq.current;
    return getArtifacts(requestSessionId)
      .then((next) => {
        if (
          mounted.current &&
          currentSessionId.current === requestSessionId &&
          seq === artifactListSeq.current
        ) {
          setArtifacts(next);
          return next;
        }
        return null;
      })
      .catch(() => {
        if (
          mounted.current &&
          currentSessionId.current === requestSessionId &&
          seq === artifactListSeq.current
        ) {
          setArtifacts([]);
          return [];
        }
        return null;
      });
  };

  useEffect(() => {
    if (!active) return;
    if (showArtifacts) refreshArtifacts();
    return () => {
      artifactListSeq.current += 1;
    };
  }, [active, sessionId, refreshKey, showArtifacts]);

  useEffect(() => {
    if (!active) return;
    const requestSessionId = sessionId;
    const seq = ++rootListSeq.current;
    getRoots(requestSessionId)
      .then((next) => {
        if (
          mounted.current &&
          currentSessionId.current === requestSessionId &&
          seq === rootListSeq.current
        ) {
          setRootDirs(next);
        }
      })
      .catch(() => {
        if (
          mounted.current &&
          currentSessionId.current === requestSessionId &&
          seq === rootListSeq.current
        ) {
          setRootDirs([]);
        }
      });
    return () => {
      if (seq === rootListSeq.current) rootListSeq.current += 1;
    };
  }, [active, sessionId, refreshKey]);

  useEffect(() => {
    setSelected(null);
    setLoadedContent(null);
    setArtifacts([]);
    setRootDirs([]);
  }, [sessionId]);

  const readSelected = (target: ArtifactInfo) =>
    target.origin
      ? readArtifact(sessionId, target.path, target.origin)
      : readArtifact(sessionId, target.path);

  useEffect(() => {
    const seq = ++artifactReadSeq.current;
    setLoadedContent(null);
    if (!selected || !selectedReadKey) return;
    const current = selected;
    const currentKey = selectedReadKey;
    readSelected(current)
      .then((next) => {
        if (mounted.current && seq === artifactReadSeq.current) {
          setLoadedContent({ key: currentKey, value: next });
        }
      })
      .catch((err) => {
        if (!mounted.current || seq !== artifactReadSeq.current) return;
        setLoadedContent({
          key: currentKey,
          value: unavailableContent(current, err, unavailableMessage),
        });
      });
  }, [selectedReadKey, unavailableMessage]);

  const reloadSelected = () => {
    if (!selected) return Promise.resolve();
    const seq = ++artifactReadSeq.current;
    const current = selected;
    const currentKey = readKey(sessionId, current);
    if (!currentKey) return Promise.resolve();
    setLoadedContent(null);
    return readSelected(current)
      .then((next) => {
        if (mounted.current && seq === artifactReadSeq.current) {
          setLoadedContent({ key: currentKey, value: next });
        }
      })
      .catch((err) => {
        if (mounted.current && seq === artifactReadSeq.current) {
          setLoadedContent({
            key: currentKey,
            value: unavailableContent(current, err, unavailableMessage),
          });
        }
      });
  };

  const openArtifactPath = (path: string) => {
    const minimal = (candidate: string): ArtifactInfo => ({
      path: candidate,
      name: candidate.split("/").pop() || candidate,
      kind: kindFromPath(candidate),
      size: 0,
      modified_at: 0,
    });
    const match = (list: ArtifactInfo[], candidate: string) => {
      const exact = list.find((artifact) => artifact.path === candidate);
      if (exact) return exact;
      const byBasename = list.filter((artifact) => artifact.name === candidate);
      return byBasename.length === 1 ? byBasename[0] : undefined;
    };
    const found = match(artifacts, path);
    if (found) {
      setSelected(found);
      return;
    }
    refreshArtifacts().then((list) => {
      if (!list) return;
      setSelected(match(list, path) ?? minimal(path));
    });
  };

  useEffect(() => {
    if (!artifactOpenRequest?.path) return;
    openArtifactPath(artifactOpenRequest.path);
    onArtifactOpenConsumed?.();
  }, [artifactOpenRequest?.nonce, artifactOpenRequest?.path]);

  const openEntry = (path: string) =>
    setSelected({
      path,
      name: path.split("/").pop() || path,
      kind: kindFromPath(path),
      size: 0,
      modified_at: 0,
      origin: selected?.origin,
    });

  return {
    artifacts,
    rootDirs,
    selected,
    content,
    selectArtifact: setSelected,
    closeArtifact: () => setSelected(null),
    openEntry,
    refreshArtifacts,
    reloadSelected,
  };
}
