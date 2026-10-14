import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSessionStartPayload, translationProviderUsesModel,
  type SessionStartInputs,
} from "../src/sessionStartPayload.ts";

const base: SessionStartInputs = {
  selectedProjectId: "all",
  recordAudio: true,
  needsAudio: false,
  sourceLanguage: "ko",
  targetLanguages: ["en"],
  engine: "mock",
  resourceProfile: "low",
  correctionProvider: "none",
  correctionModel: "ignored",
  translationProvider: "none",
  translationModel: "ignored",
  title: "Church",
  presenter: "",
  preset: "church",
  hotwords: "성령, 은혜\n요한복음, ",
  referenceDocuments: [],
  displaySettings: {
    font_family: "system", font_scale_percent: 100, max_lines: 2,
    hold_seconds: 8, show_source_when_translated: true, text_align: "center",
  },
};

test("mock/source-only request never records audio or attaches unused provider models", () => {
  const payload = buildSessionStartPayload(base);
  assert.equal(payload.project_id, null);
  assert.equal(payload.record_audio, false);
  assert.equal(payload.translation_model, null);
  assert.equal(payload.correction_model, null);
  assert.deepEqual(payload.context.hotwords, ["성령", "은혜", "요한복음"]);
  assert.deepEqual(payload.context.glossary, []);
  assert.equal(payload.context.presenter, null);
});

test("explicit opted-in live sermon retains project, recording and models", () => {
  const payload = buildSessionStartPayload({
    ...base,
    selectedProjectId: "project-1", needsAudio: true,
    engine: "faster-whisper", correctionProvider: "ollama",
    translationProvider: "ollama", translationModel: "local-translator",
    correctionModel: "local-corrector",
  });
  assert.equal(payload.project_id, "project-1");
  assert.equal(payload.record_audio, true);
  assert.equal(payload.correction_model, "local-corrector");
  assert.equal(payload.translation_model, "local-translator");
  assert.equal(payload.context.audience_access, true);
  assert.deepEqual(payload.context.output_modes, ["audience", "projector", "obs"]);
});

test("EN->KO missionary session does not leak unrelated Korean glossary fields", () => {
  const input = {
    ...base, selectedProjectId: "unassigned", sourceLanguage: "en",
    targetLanguages: ["ko", "ja"], hotwords: "Holy Spirit\nBaptism",
    translationProvider: "m2m100-ct2", needsAudio: true,
    recordAudio: false,
  };
  const payload = buildSessionStartPayload(input);
  assert.equal(payload.source_language, "en");
  assert.deepEqual(payload.target_languages, ["ko", "ja"]);
  assert.deepEqual(payload.context.hotwords, ["Holy Spirit", "Baptism"]);
  assert.equal(payload.context.glossary.length, 0);
  assert.equal(payload.translation_model, null);
  assert.equal(payload.record_audio, false);
  assert.equal(payload.project_id, null);
});

test("translation model IDs attach only to explicitly model-backed providers", () => {
  assert.equal(translationProviderUsesModel("ollama"), true);
  assert.equal(translationProviderUsesModel("openai-compatible"), true);
  for (const provider of ["none", "demo", "m2m100-ct2"]) {
    assert.equal(translationProviderUsesModel(provider), false);
    assert.equal(buildSessionStartPayload({
      ...base, translationProvider: provider,
    }).translation_model, null);
  }
});
