import type { Locale } from "./locales";

export interface GlossaryCopy {
  loadFailed: string;
  saveFailed: string;
  updateFailed: string;
  deleteFailed: string;
  churchFailed: string;
  exported: string;
  exportFailed: string;
  importType: string;
  importDone: string;
  created: string;
  updated: string;
  skipped: string;
  importFailed: string;
  title: string;
  currentPrefix: string;
  currentSuffix: string;
  importChurch: string;
  importJsonCsv: string;
  exportJson: string;
  exportCsv: string;
  conflict: string;
  upsert: string;
  skip: string;
  validationHelp: string;
  termPlaceholder: string;
  aliasesPlaceholder: string;
  translationOptional: string;
  categoryPlaceholder: string;
  globalScope: string;
  add: string;
  empty: string;
  aliases: string;
  noAliases: string;
  applies: string;
  allPresets: string;
  disable: string;
  enable: string;
  delete: string;
  edit: string;
  save: string;
  cancel: string;
  sourceLanguage: string;
  korean: string;
  english: string;
  japanese: string;
  boostLabel: string;
  presetScope: string;
  choosePreset: string;
  pageTitle: string;
  pageHelp: string;
  projectScope: string;
  globalTerms: string;
  backToLive: string;
  managePage: string;
}

export const GLOSSARY_COPY: Record<Locale, GlossaryCopy> = {
  en: {
    loadFailed: "Could not load the glossary.", saveFailed: "Could not save the term.", updateFailed: "Could not update the term state.", deleteFailed: "Could not delete the term.",
    churchFailed: "Could not import the church preset terms.", exported: "glossary exported.", exportFailed: "Could not export the glossary.", importType: "Only JSON or CSV files can be imported.",
    importDone: "Import complete", created: "created", updated: "updated", skipped: "skipped", importFailed: "Could not import the glossary.", title: "Glossary",
    currentPrefix: "Currently", currentSuffix: "terms apply to this session", importChurch: "Import church preset terms", importJsonCsv: "Import JSON / CSV", exportJson: "Export JSON", exportCsv: "Export CSV",
    conflict: "Duplicate term handling", upsert: "Update existing entries", skip: "Skip existing entries", validationHelp: "The whole file is validated before import. Existing IDs are preserved when updating.",
    termPlaceholder: "Canonical term · e.g. Gospel of John", aliasesPlaceholder: "Misrecognitions / aliases · comma-separated", translationOptional: "translation · optional", categoryPlaceholder: "Category",
    globalScope: "Use in every preset", add: "Add term", empty: "No saved terms.", aliases: "Aliases", noAliases: "No aliases", applies: "Applies to", allPresets: "all presets", disable: "Disable", enable: "Enable", delete: "Delete", edit: "Edit", save: "Save changes", cancel: "Cancel", sourceLanguage: "Spoken/source language", korean: "Korean", english: "English", japanese: "Japanese", boostLabel: "Term boost", presetScope: "Apply to presets", choosePreset: "Choose at least one preset or global scope.", pageTitle: "Glossary workspace", pageHelp: "Manage recognition aliases and Korean, English, and Japanese terminology across sessions.", projectScope: "Glossary scope", globalTerms: "Global and preset terms", backToLive: "Back to live", managePage: "Manage glossary",
  },
  ko: {
    loadFailed: "용어집을 불러오지 못했습니다.", saveFailed: "용어를 저장하지 못했습니다.", updateFailed: "용어 상태를 변경하지 못했습니다.", deleteFailed: "용어를 삭제하지 못했습니다.",
    churchFailed: "교회 기본 용어를 가져오지 못했습니다.", exported: "용어집을 내보냈습니다.", exportFailed: "용어집을 내보내지 못했습니다.", importType: "JSON 또는 CSV 파일만 가져올 수 있습니다.",
    importDone: "가져오기 완료", created: "생성", updated: "갱신", skipped: "건너뜀", importFailed: "용어집을 가져오지 못했습니다.", title: "용어집",
    currentPrefix: "현재", currentSuffix: "개 적용", importChurch: "교회 기본 용어 가져오기", importJsonCsv: "JSON / CSV 가져오기", exportJson: "JSON 내보내기", exportCsv: "CSV 내보내기",
    conflict: "중복 용어 처리", upsert: "기존 항목 업데이트", skip: "기존 항목 건너뛰기", validationHelp: "가져오기 전에 파일 전체를 검증합니다. 업데이트 시 기존 ID는 유지됩니다.",
    termPlaceholder: "표준 용어 · 예: 요한복음", aliasesPlaceholder: "오인식/별칭 · 쉼표로 구분", translationOptional: "번역 · 선택 사항", categoryPlaceholder: "카테고리",
    globalScope: "모든 preset에서 사용", add: "용어 추가", empty: "저장된 용어가 없습니다.", aliases: "별칭", noAliases: "별칭 없음", applies: "적용", allPresets: "전체 preset", disable: "끄기", enable: "켜기", delete: "삭제", edit: "수정", save: "변경 저장", cancel: "취소", sourceLanguage: "설교·발화 원문 언어", korean: "한국어", english: "영어", japanese: "일본어", boostLabel: "용어 가중치", presetScope: "적용할 프리셋", choosePreset: "하나 이상의 프리셋 또는 전체 적용을 선택하세요.", pageTitle: "용어사전 관리", pageHelp: "음성 인식 별칭과 한국어·영어·일본어 번역을 세션 전반에서 관리합니다.", projectScope: "용어사전 범위", globalTerms: "전역·프리셋 용어", backToLive: "실시간 화면으로", managePage: "용어사전 관리",
  },
  ja: {
    loadFailed: "用語集を読み込めませんでした。", saveFailed: "用語を保存できませんでした。", updateFailed: "用語の状態を変更できませんでした。", deleteFailed: "用語を削除できませんでした。",
    churchFailed: "教会向け既定用語を読み込めませんでした。", exported: "用語集をエクスポートしました。", exportFailed: "用語集をエクスポートできませんでした。", importType: "JSONまたはCSVファイルのみインポートできます。",
    importDone: "インポート完了", created: "作成", updated: "更新", skipped: "スキップ", importFailed: "用語集をインポートできませんでした。", title: "用語集",
    currentPrefix: "現在", currentSuffix: "件を適用", importChurch: "教会向け既定用語を追加", importJsonCsv: "JSON / CSVをインポート", exportJson: "JSONをエクスポート", exportCsv: "CSVをエクスポート",
    conflict: "重複用語の処理", upsert: "既存項目を更新", skip: "既存項目をスキップ", validationHelp: "インポート前にファイル全体を検証します。更新時も既存IDは維持されます。",
    termPlaceholder: "標準用語 · 例: ヨハネによる福音書", aliasesPlaceholder: "誤認識 / 別名 · カンマ区切り", translationOptional: "翻訳 · 任意", categoryPlaceholder: "カテゴリ",
    globalScope: "すべてのpresetで使用", add: "用語を追加", empty: "保存済み用語はありません。", aliases: "別名", noAliases: "別名なし", applies: "適用", allPresets: "全preset", disable: "無効", enable: "有効", delete: "削除", edit: "編集", save: "変更を保存", cancel: "キャンセル", sourceLanguage: "説教・発話の言語", korean: "韓国語", english: "英語", japanese: "日本語", boostLabel: "用語の重み", presetScope: "適用するプリセット", choosePreset: "プリセットを一つ以上選択するか、全体適用にしてください。", pageTitle: "用語集の管理", pageHelp: "音声認識の別名と韓国語・英語・日本語訳を管理します。", projectScope: "用語集の範囲", globalTerms: "共通・プリセットの用語", backToLive: "ライブ画面へ戻る", managePage: "用語集を管理",
  },
};
