const STEPS = [
  { key: "upload", label: "Tải sách", statuses: ["uploaded"] },
  { key: "topics", label: "Chủ đề", statuses: ["extracting_topics", "reviewing_topics"] },
  { key: "lessons", label: "Bài học", statuses: ["extracting_lessons", "reviewing_lessons"] },
  { key: "chunks", label: "Chunk", statuses: ["extracting_chunks", "reviewing_chunks"] },
  {
    key: "bundle",
    label: "Hoàn tất",
    statuses: ["preparing_bundle", "running_kaggle", "extracting_keywords", "bundle_ready", "importing_mongodb", "mongodb_imported"],
  },
];

function progressIndex(status) {
  if (status === "mongodb_imported") return STEPS.length;
  if (status === "uploaded") return 1;
  const index = STEPS.findIndex((step) => step.statuses.includes(status));
  return index >= 0 ? index : 0;
}

export default function ReviewStepper({ status, activeStep = "upload", onStepChange }) {
  const progress = progressIndex(status);
  return (
    <ol className="stepper review-stepper" aria-label="Các bước duyệt metadata">
      {STEPS.map((step, index) => {
        const isSelected = activeStep === step.key;
        const state = status === "error" && isSelected ? "error" : isSelected ? "active" : index < progress ? "done" : "pending";
        return (
          <li key={step.key} className={`stepItem ${state}`}>
            <button type="button" className="stepButton" onClick={() => onStepChange?.(step.key)}>
            <span className="stepDot">{state === "done" ? "✓" : index + 1}</span>
            <span className="stepText">
              <strong>{step.label}</strong>
            </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
