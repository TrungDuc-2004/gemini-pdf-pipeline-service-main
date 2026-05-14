export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8100";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  const text = await response.text();
  let data = null;

  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }

  if (!response.ok) {
    const detail = data?.detail || data?.message || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  return data;
}

export function itemsFromResponse(data, key = "items") {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.[key])) return data[key];
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

export function health() {
  return request("/health");
}

export function listJobs() {
  return request("/api/jobs");
}

export function createJob(formData) {
  return request("/api/jobs", {
    method: "POST",
    body: formData,
  });
}

export function getJob(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function getStatus(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/status`);
}

export function getLogs(jobId, lines = 200) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/logs?lines=${encodeURIComponent(lines)}`);
}

export function retryGeminiStage(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/retry-gemini-stage`, { method: "POST" });
}

export function extractTopics(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/extract/topics`, { method: "POST" });
}

export async function getTopics(jobId) {
  const raw = await request(`/api/jobs/${encodeURIComponent(jobId)}/topics`);
  const nested = raw?.data && typeof raw.data === "object" ? raw.data : {};
  const topics =
    Array.isArray(raw?.topics) ? raw.topics :
    Array.isArray(nested?.topics) ? nested.topics :
    Array.isArray(raw) ? raw :
    [];
  return {
    ...raw,
    ok: raw?.ok !== false,
    topics,
    approved: Boolean(raw?.approved ?? raw?.approved_all ?? nested?.approved ?? nested?.approved_all),
    approved_topic_nums: Array.isArray(raw?.approved_topic_nums)
      ? raw.approved_topic_nums
      : Array.isArray(nested?.approved_topic_nums) ? nested.approved_topic_nums : [],
    pending_topic_nums: Array.isArray(raw?.pending_topic_nums)
      ? raw.pending_topic_nums
      : Array.isArray(nested?.pending_topic_nums) ? nested.pending_topic_nums : [],
    raw,
  };
}

export function getTopicPreviewUrl(jobId, topicNum) {
  return `${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/topics/${encodeURIComponent(topicNum)}/preview`;
}

export function getAssetPreviewUrl(objectKey) {
  return `${API_BASE_URL}/api/assets/preview?object_key=${encodeURIComponent(objectKey)}`;
}

export function getSourcePreviewUrl(jobId) {
  return `${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/source/preview`;
}

export function getLessonPreviewUrl(jobId, lessonNum) {
  return `${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/lessons/${encodeURIComponent(lessonNum)}/preview`;
}

export function getChunkPreviewUrl(jobId, chunkId) {
  return `${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/chunks/${encodeURIComponent(chunkId)}/preview`;
}

export function getTopicPreviewInfo(jobId, topicNum) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/topics/${encodeURIComponent(topicNum)}/preview-info`);
}

export function saveTopics(jobId, topics) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/topics`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topics }),
  });
}

export function approveTopics(jobId, topics) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/topics/approve`, {
    method: "POST",
    headers: topics ? { "Content-Type": "application/json" } : undefined,
    body: topics ? JSON.stringify({ topics }) : undefined,
  });
}

export function approveTopic(jobId, topicNum) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/topics/${encodeURIComponent(topicNum)}/approve`, {
    method: "POST",
  });
}

export function extractLessons(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/extract/lessons`, { method: "POST" });
}

export function extractLessonsForTopic(jobId, topicNum) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/topics/${encodeURIComponent(topicNum)}/extract-lessons`, { method: "POST" });
}

export function getLessons(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/lessons`);
}

export function saveLessons(jobId, lessons) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/lessons`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lessons }),
  });
}

export function approveLessons(jobId, lessons) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/lessons/approve`, {
    method: "POST",
    headers: lessons ? { "Content-Type": "application/json" } : undefined,
    body: lessons ? JSON.stringify({ lessons }) : undefined,
  });
}

export function approveLesson(jobId, lessonNum) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/lessons/${encodeURIComponent(lessonNum)}/approve`, { method: "POST" });
}

export function extractChunks(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/extract/chunks`, { method: "POST" });
}

export function extractChunksForLesson(jobId, lessonNum) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/lessons/${encodeURIComponent(lessonNum)}/extract-chunks`, { method: "POST" });
}

export function getChunks(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks`);
}

export function saveChunks(jobId, chunks) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chunks }),
  });
}

export function addChunk(jobId, payload) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deleteChunk(jobId, chunkId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks/${encodeURIComponent(chunkId)}`, {
    method: "DELETE",
  });
}

export function recutChunk(jobId, payload) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks/recut`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function approveChunks(jobId, chunks) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks/approve`, {
    method: "POST",
    headers: chunks ? { "Content-Type": "application/json" } : undefined,
    body: chunks ? JSON.stringify({ chunks }) : undefined,
  });
}

export function approveChunkIds(jobId, chunkIds) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chunk_ids: chunkIds }),
  });
}

export function approveChunk(jobId, chunkId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks/${encodeURIComponent(chunkId)}/approve`, { method: "POST" });
}

export function finalizeChunksAfterKaggle(jobId, options = {}) {
  const params = new URLSearchParams();
  if (options.force_without_kaggle) params.set("force_without_kaggle", "true");
  const query = params.toString();
  return request(`/api/jobs/${encodeURIComponent(jobId)}/chunks/finalize-after-kaggle${query ? `?${query}` : ""}`, { method: "POST" });
}

export function prepareBundle(jobId, options = {}) {
  const params = new URLSearchParams();
  if (options.skip_kaggle) params.set("skip_kaggle", "true");
  if (options.skip_keywords) params.set("skip_keywords", "true");
  if (options.retry_failed_keywords_only) params.set("retry_failed_keywords_only", "true");
  const query = params.toString();
  return request(`/api/jobs/${encodeURIComponent(jobId)}/prepare-bundle${query ? `?${query}` : ""}`, { method: "POST" });
}

export function getBundle(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/bundle`);
}

export function downloadBundle(jobId) {
  window.open(`${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/bundle/download`, "_blank", "noopener,noreferrer");
}

export function importMongo(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/import-mongodb`, { method: "POST" });
}

export const importMongoDb = importMongo;

export function getMongoImportResult(jobId) {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/mongo-import-result`);
}
