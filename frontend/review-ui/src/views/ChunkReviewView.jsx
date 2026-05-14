import { useMemo, useState } from "react";
import LogPanel from "../components/LogPanel.jsx";
import RawJsonPanel from "../components/RawJsonPanel.jsx";

const CHUNK_COLUMNS = [
  "chunk_num",
  "chunk_name",
  "heading",
  "title",
  "start",
  "end",
  "content_head",
];

function groupChunks(chunks = [], grouped = []) {
  if (Array.isArray(grouped) && grouped.length > 0) {
    return grouped.map((group) => ({
      lesson_num: group.lesson_num ?? "",
      lesson_name: group.lesson_name ?? "",
      lesson_stem: group.lesson_stem ?? "",
      chunks: Array.isArray(group.chunks) ? group.chunks : [],
    }));
  }

  const map = new Map();
  chunks.forEach((chunk) => {
    const key = `${chunk.lesson_stem ?? ""}::${chunk.lesson_num ?? ""}::${chunk.lesson_name ?? ""}`;
    if (!map.has(key)) {
      map.set(key, {
        lesson_num: chunk.lesson_num ?? "",
        lesson_name: chunk.lesson_name ?? "",
        lesson_stem: chunk.lesson_stem ?? "",
        chunks: [],
      });
    }
    map.get(key).chunks.push(chunk);
  });
  return Array.from(map.values());
}

function chunkGlobalIndex(chunks, selectedChunk) {
  if (!selectedChunk) return -1;
  return (Array.isArray(chunks) ? chunks : []).findIndex(
    (chunk) =>
      chunk === selectedChunk ||
      (chunk.chunk_id && chunk.chunk_id === selectedChunk.chunk_id) ||
      (chunk.id && chunk.id === selectedChunk.id)
  );
}

export default function ChunkReviewView({
  jobId,
  chunks,
  groupedByLesson,
  approved,
  rawChunksResponse,
  rawOpen,
  onToggleRaw,
  onChangeChunk,
  onSave,
  onApprove,
  onReload,
  onLoadLogs,
  onAddChunk,
  onDeleteChunk,
  onRecutChunk,
  log,
  loading,
}) {
  const groups = useMemo(
    () => groupChunks(chunks || [], groupedByLesson || []),
    [chunks, groupedByLesson]
  );
  const [selectedLessonIndex, setSelectedLessonIndex] = useState(0);
  const [selectedChunkId, setSelectedChunkId] = useState("");
  const [addForm, setAddForm] = useState({
    chunk_num: "",
    chunk_name: "",
    title: "",
    start: 1,
    end: 1,
    heading: "",
    content_head: false,
  });

  if (chunks == null) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Danh sách chunk</h2>
          <button type="button" onClick={onReload} disabled={loading}>
            Tải danh sách chunk
          </button>
        </div>
        <p className="muted">
          {loading ? "Đang tải danh sách chunk..." : "Chưa tải danh sách chunk."}
        </p>
      </section>
    );
  }

  const safeChunks = Array.isArray(chunks) ? chunks : [];

  const selectedGroup = groups[Math.min(selectedLessonIndex, Math.max(groups.length - 1, 0))] || {
    chunks: [],
  };
  const selectedChunk =
    selectedGroup.chunks.find((chunk) => (chunk.chunk_id || chunk.id) === selectedChunkId) ||
    selectedGroup.chunks[0] ||
    safeChunks[0] ||
    null;
  const selectedIndex = chunkGlobalIndex(safeChunks, selectedChunk);

  function updateAddField(field, value) {
    setAddForm((current) => ({ ...current, [field]: value }));
  }

  async function submitAddChunk(event) {
    event.preventDefault();
    if (!selectedGroup.lesson_stem && !selectedGroup.lesson_num) return;
    await onAddChunk({
      ...addForm,
      lesson_stem: selectedGroup.lesson_stem,
      lesson_num: selectedGroup.lesson_num,
      title: addForm.title || addForm.chunk_name,
      start: Number(addForm.start),
      end: Number(addForm.end),
      content_head: Boolean(addForm.content_head),
    });
  }

  async function deleteSelectedChunk() {
    if (!selectedChunk) return;
    const chunkId = selectedChunk.chunk_id || selectedChunk.id;
    if (!chunkId) return;
    if (!confirm(`Xóa chunk ${chunkId}?`)) return;
    await onDeleteChunk(chunkId);
  }

  async function recutSelectedChunk() {
    if (!selectedChunk) return;
    await onRecutChunk({
      chunk_id: selectedChunk.chunk_id || selectedChunk.id,
      lesson_stem: selectedChunk.lesson_stem,
      chunk_num: selectedChunk.chunk_num,
      start: Number(selectedChunk.start),
      end: Number(selectedChunk.end),
      heading: selectedChunk.heading || "",
      title: selectedChunk.title || selectedChunk.chunk_name || "",
      content_head: Boolean(selectedChunk.content_head),
    });
  }

  return (
    <section className="panel widePanel">
      <div className="panelHeader">
        <div>
          <h2>Danh sách chunk</h2>
          <p className="muted">
            Trạng thái duyệt: <strong>{approved ? "Đã duyệt" : "Chưa duyệt"}</strong>
          </p>
        </div>
        <div className="buttonRow compact">
          <button type="button" onClick={onReload} disabled={loading}>Làm mới chunk</button>
          <button type="button" onClick={onSave} disabled={loading || approved}>Lưu chunk</button>
          <button type="button" onClick={onApprove} disabled={loading || approved || safeChunks.length === 0}>Duyệt chunk</button>
          <button type="button" onClick={onLoadLogs} disabled={loading}>Xem log</button>
        </div>
      </div>

      {!Array.isArray(chunks) ? (
        <div className="errorBox">Dữ liệu chunk không đúng định dạng danh sách.</div>
      ) : null}
      {Array.isArray(chunks) && safeChunks.length === 0 ? (
        <p className="muted">Danh sách chunk đang trống.</p>
      ) : null}

      <div className="chunkReviewGrid">
        <aside className="lessonSidebar">
          <h3>Danh sách bài học</h3>
          {groups.length === 0 ? <p className="muted">Không có nhóm bài học.</p> : null}
          {groups.map((group, index) => (
            <button
              type="button"
              key={`${group.lesson_stem}-${index}`}
              className={`topicListItem ${index === selectedLessonIndex ? "active" : ""}`}
              onClick={() => {
                setSelectedLessonIndex(index);
                setSelectedChunkId("");
              }}
            >
              <span className="topicListTitle">
                {group.lesson_num ? `Bài ${group.lesson_num}` : "Bài học"}
              </span>
              <span>{group.lesson_name || group.lesson_stem || "-"}</span>
              <span className="lessonCount">{group.chunks.length} chunk</span>
            </button>
          ))}
        </aside>

        <div className="lessonMiddle">
          <h3>Chunk trong bài học</h3>
          <div className="tableWrap denseTableWrap">
            <table className="topicTable lessonTable chunkTable">
              <thead>
                <tr>
                  <th>#</th>
                  {CHUNK_COLUMNS.map((column) => <th key={column}>{column}</th>)}
                </tr>
              </thead>
              <tbody>
                {selectedGroup.chunks.length === 0 ? (
                  <tr>
                    <td colSpan={CHUNK_COLUMNS.length + 1} className="emptyCell">
                      Không có chunk trong bài học này.
                    </td>
                  </tr>
                ) : null}
                {selectedGroup.chunks.map((chunk, localIndex) => {
                  const globalIndex = chunkGlobalIndex(safeChunks, chunk);
                  const chunkId = chunk.chunk_id || chunk.id || `${chunk.lesson_stem}:${chunk.chunk_num}`;
                  const isSelected = (selectedChunk?.chunk_id || selectedChunk?.id) === chunkId;
                  return (
                    <tr
                      key={chunkId || localIndex}
                      className={isSelected ? "selectedRow" : ""}
                      onClick={() => setSelectedChunkId(chunkId)}
                    >
                      <td className="rowNumber">{localIndex + 1}</td>
                      {CHUNK_COLUMNS.map((column) => (
                        <td key={column}>
                          {column === "content_head" ? (
                            <input
                              type="checkbox"
                              checked={Boolean(chunk[column])}
                              disabled={approved}
                              onFocus={() => setSelectedChunkId(chunkId)}
                              onChange={(event) => onChangeChunk(globalIndex, column, event.target.checked)}
                            />
                          ) : (
                            <input
                              type={column === "start" || column === "end" ? "number" : "text"}
                              value={chunk[column] ?? ""}
                              disabled={approved}
                              onFocus={() => setSelectedChunkId(chunkId)}
                              onChange={(event) => {
                                const value = column === "start" || column === "end"
                                  ? Number(event.target.value)
                                  : event.target.value;
                                onChangeChunk(globalIndex, column, value);
                              }}
                            />
                          )}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <form className="addChunkForm" onSubmit={submitAddChunk}>
            <h3>Thêm chunk</h3>
            <div className="addChunkGrid">
              <label>
                <span>chunk_num</span>
                <input value={addForm.chunk_num} onChange={(e) => updateAddField("chunk_num", e.target.value)} />
              </label>
              <label>
                <span>chunk_name</span>
                <input value={addForm.chunk_name} onChange={(e) => updateAddField("chunk_name", e.target.value)} />
              </label>
              <label>
                <span>title</span>
                <input value={addForm.title} onChange={(e) => updateAddField("title", e.target.value)} />
              </label>
              <label>
                <span>heading</span>
                <input value={addForm.heading} onChange={(e) => updateAddField("heading", e.target.value)} />
              </label>
              <label>
                <span>start</span>
                <input type="number" value={addForm.start} onChange={(e) => updateAddField("start", e.target.value)} />
              </label>
              <label>
                <span>end</span>
                <input type="number" value={addForm.end} onChange={(e) => updateAddField("end", e.target.value)} />
              </label>
              <label className="checkboxRow">
                <input
                  type="checkbox"
                  checked={addForm.content_head}
                  onChange={(e) => updateAddField("content_head", e.target.checked)}
                />
                <span>content_head</span>
              </label>
            </div>
            <button type="submit" disabled={loading || approved || (!selectedGroup.lesson_stem && !selectedGroup.lesson_num)}>
              Thêm chunk
            </button>
          </form>
        </div>

        <aside className="lessonDetail">
          <h3>Chi tiết chunk</h3>
          {selectedChunk ? (
            <>
              <dl className="statusGrid detailGrid">
                <dt>chunk_id</dt>
                <dd className="mono breakText">{selectedChunk.chunk_id || selectedChunk.id || "-"}</dd>
                <dt>lesson</dt>
                <dd>{selectedChunk.lesson_num ? `Bài ${selectedChunk.lesson_num}` : "-"}</dd>
                <dt>chunk_num</dt>
                <dd>{selectedChunk.chunk_num || "-"}</dd>
                <dt>pages</dt>
                <dd>{selectedChunk.start} - {selectedChunk.end}</dd>
                <dt>content_head</dt>
                <dd>{String(Boolean(selectedChunk.content_head))}</dd>
                <dt>pdf</dt>
                <dd className="mono breakText">{selectedChunk.pdf_path || selectedChunk.chunk_pdf || "-"}</dd>
              </dl>
              <div className="buttonRow">
                <button type="button" onClick={deleteSelectedChunk} disabled={loading || approved}>Xóa chunk</button>
                <button type="button" onClick={recutSelectedChunk} disabled={loading || approved}>Cắt lại chunk</button>
              </div>
            </>
          ) : (
            <p className="muted">Chưa chọn chunk.</p>
          )}
          <RawJsonPanel
            title="JSON gốc"
            data={rawChunksResponse}
            open={rawOpen}
            onToggle={onToggleRaw}
          />
          <LogPanel log={log} onRefresh={onLoadLogs} loading={loading} />
        </aside>
      </div>
    </section>
  );
}
