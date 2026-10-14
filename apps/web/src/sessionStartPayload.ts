import type {
  CaptionDisplaySettings, ProductPreset, ReferenceDocument, SessionContext,
} from "./types";

/** Pure operator->backend session request contract, independent from React state. */
export interface SessionStartInputs {
  selectedProjectId: string;
  recordAudio: boolean;
  needsAudio: boolean;
  sourceLanguage: string;
  targetLanguages: string[];
  engine: string;
  resourceProfile: "standard" | "low";
  correctionProvider: string;
  correctionModel: string;
  translationProvider: string;
  translationModel: string;
  title: string;
  presenter: string;
  preset: ProductPreset;
  hotwords: string;
  referenceDocuments: ReferenceDocument[];
  displaySettings: CaptionDisplaySettings;
}

export function translationProviderUsesModel(provider: string): boolean {
  return provider === "ollama" || provider === "openai-compatible";
}

export function buildSessionStartPayload(input: SessionStartInputs) {
  const context: SessionContext = {
    title: input.title,
    presenter: input.presenter || null,
    preset: input.preset,
    description: "",
    hotwords: input.hotwords.split(/[,\n]/).map((word) => word.trim()).filter(Boolean),
    // Source-scoped glossary selection and project overrides are resolved by
    // the server; never copy UI global glossary across a foreign-language session.
    glossary: [],
    reference_documents: input.referenceDocuments,
    reference_text: "",
    output_modes: ["audience", "projector", "obs"],
    audience_access: true,
    display_settings: input.displaySettings,
  };
  return {
    project_id: input.selectedProjectId !== "all"
      && input.selectedProjectId !== "unassigned"
      ? input.selectedProjectId
      : null,
    record_audio: input.recordAudio && input.needsAudio,
    source_language: input.sourceLanguage,
    target_languages: input.targetLanguages,
    engine: input.engine,
    resource_profile: input.resourceProfile,
    correction_provider: input.correctionProvider,
    correction_model: input.correctionProvider === "ollama" ? input.correctionModel : null,
    translation_provider: input.translationProvider,
    translation_model: translationProviderUsesModel(input.translationProvider)
      ? input.translationModel
      : null,
    context,
  };
}
