import { useMemo, useState } from "react";
import LogPanel from "../components/LogPanel.jsx";
import RawJsonPanel from "../components/RawJsonPanel.jsx";

const LESSON_COLUMNS = [
  "lesson_num",
  "lesson_name",
  "start",
  "end",
  "raw_heading",
  "raw_title",
];

function groupLessons(lessons = [], grouped = []) {
  if (Array.isArray(grouped) && grouped.length > 0) {
    return grouped.map((group) => ({
      topic_num: group.topic_num ?? "",
      topic_name: group.topic_name ?? "",
      lessons: Array.isArray(group.lessons) ? group.lessons : [],
    }));
  }

  const map = new Map();
  lessons.forEach((lesson) => {
    const key = `${lesson.topic_num ?? ""}::${lesson.topic_name ?? ""}`;
    if (!map.has(key)) {
      map.set(key, {
        topic_num: lesson.topic_num ?? "",
        topic_name: lesson.topic_name ?? "",
        lessons: [],
      });
    }
    map.get(key).lessons.push(lesson);
  });
  return Array.from(map.values());
}

function lessonGlobalIndex(lessons, selectedLesson) {
  if (!selectedLesson) return -1;
  return (Array.isArray(lessons) ? lessons : []).findIndex(
    (lesson) => lesson === selectedLesson || lesson.name === selectedLesson.name
  );
}

export default function LessonReviewView({
  lessons,
  groupedByTopic,
  approved,
  rawLessonsResponse,
  rawOpen,
  onToggleRaw,
  onChangeLesson,
  onSave,
  onApprove,
  onReload,
  onLoadLogs,
  log,
  loading,
}) {
  const groups = useMemo(
    () => groupLessons(lessons || [], groupedByTopic || []),
    [lessons, groupedByTopic]
  );
  const [selectedTopicIndex, setSelectedTopicIndex] = useState(0);
  const [selectedLessonName, setSelectedLessonName] = useState("");

  if (lessons == null) {
    return (
      <section className="panel">
        <div className="panelHeader">
          <h2>Danh sách bài học</h2>
          <button type="button" onClick={onReload} disabled={loading}>
            Tải danh sách bài học
          </button>
        </div>
        <p className="muted">
          {loading ? "Đang tải danh sách bài học..." : "Chưa tải danh sách bài học."}
        </p>
      </section>
    );
  }

  const safeLessons = Array.isArray(lessons) ? lessons : [];

  const selectedGroup = groups[Math.min(selectedTopicIndex, Math.max(groups.length - 1, 0))] || {
    lessons: [],
  };
  const selectedLesson =
    selectedGroup.lessons.find((lesson) => lesson.name === selectedLessonName) ||
    selectedGroup.lessons[0] ||
    safeLessons[0] ||
    null;
  const selectedIndex = lessonGlobalIndex(safeLessons, selectedLesson);

  return (
    <section className="panel widePanel">
      <div className="panelHeader">
        <div>
          <h2>Danh sách bài học</h2>
          <p className="muted">
            Trạng thái duyệt: <strong>{approved ? "Đã duyệt" : "Chưa duyệt"}</strong>
          </p>
        </div>
        <div className="buttonRow compact">
          <button type="button" onClick={onReload} disabled={loading}>Làm mới bài học</button>
          <button type="button" onClick={onSave} disabled={loading || approved}>Lưu bài học</button>
          <button type="button" onClick={onApprove} disabled={loading || approved || safeLessons.length === 0}>Duyệt bài học</button>
          <button type="button" onClick={onLoadLogs} disabled={loading}>Xem log</button>
        </div>
      </div>

      {!Array.isArray(lessons) ? (
        <div className="errorBox">Dữ liệu bài học không đúng định dạng danh sách.</div>
      ) : null}
      {Array.isArray(lessons) && safeLessons.length === 0 ? (
        <p className="muted">Danh sách bài học đang trống.</p>
      ) : null}

      <div className="lessonReviewGrid">
        <aside className="lessonSidebar">
          <h3>Chủ đề</h3>
          {groups.length === 0 ? <p className="muted">Không có nhóm chủ đề.</p> : null}
          {groups.map((group, index) => (
            <button
              type="button"
              key={`${group.topic_num}-${group.topic_name}-${index}`}
              className={`topicListItem ${index === selectedTopicIndex ? "active" : ""}`}
              onClick={() => {
                setSelectedTopicIndex(index);
                setSelectedLessonName("");
              }}
            >
              <span className="topicListTitle">
                {group.topic_num ? `Chủ đề ${group.topic_num}` : "Chủ đề"}
              </span>
              <span>{group.topic_name || "-"}</span>
              <span className="lessonCount">{group.lessons.length} bài học</span>
            </button>
          ))}
        </aside>

        <div className="lessonMiddle">
          <h3>Bài học thuộc chủ đề</h3>
          <div className="tableWrap denseTableWrap">
            <table className="topicTable lessonTable">
              <thead>
                <tr>
                  <th>#</th>
                  {LESSON_COLUMNS.map((column) => <th key={column}>{column}</th>)}
                </tr>
              </thead>
              <tbody>
                {selectedGroup.lessons.length === 0 ? (
                  <tr>
                    <td colSpan={LESSON_COLUMNS.length + 1} className="emptyCell">
                      Không có bài học trong chủ đề này.
                    </td>
                  </tr>
                ) : null}
                {selectedGroup.lessons.map((lesson, localIndex) => {
                  const globalIndex = lessonGlobalIndex(safeLessons, lesson);
                  const isSelected = selectedLesson?.name === lesson.name;
                  return (
                    <tr
                      key={lesson.name || localIndex}
                      className={isSelected ? "selectedRow" : ""}
                      onClick={() => setSelectedLessonName(lesson.name)}
                    >
                      <td className="rowNumber">{localIndex + 1}</td>
                      {LESSON_COLUMNS.map((column) => (
                        <td key={column}>
                          <input
                            type={column === "start" || column === "end" ? "number" : "text"}
                            value={lesson[column] ?? ""}
                            disabled={approved}
                            onFocus={() => setSelectedLessonName(lesson.name)}
                            onChange={(event) => {
                              const value = column === "start" || column === "end"
                                ? Number(event.target.value)
                                : event.target.value;
                              onChangeLesson(globalIndex, column, value);
                            }}
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="lessonDetail">
          <h3>Chi tiết bài học</h3>
          {selectedLesson ? (
            <dl className="statusGrid detailGrid">
              <dt>lesson_num</dt>
              <dd>{selectedLesson.lesson_num || "-"}</dd>
              <dt>lesson_name</dt>
              <dd>{selectedLesson.lesson_name || "-"}</dd>
              <dt>topic</dt>
              <dd>{selectedLesson.topic_num ? `Chủ đề ${selectedLesson.topic_num}` : "-"}</dd>
              <dt>pages</dt>
              <dd>{selectedLesson.start} - {selectedLesson.end}</dd>
              <dt>name</dt>
              <dd className="mono">{selectedLesson.name || "-"}</dd>
              <dt>index</dt>
              <dd>{selectedIndex >= 0 ? selectedIndex + 1 : "-"}</dd>
            </dl>
          ) : (
            <p className="muted">Chưa chọn bài học.</p>
          )}
          <RawJsonPanel
            title="JSON gốc"
            data={rawLessonsResponse}
            open={rawOpen}
            onToggle={onToggleRaw}
          />
          <LogPanel log={log} onRefresh={onLoadLogs} loading={loading} />
        </aside>
      </div>
    </section>
  );
}
