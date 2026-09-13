from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/web/src/App.tsx"


def replace_once(old: str, new: str) -> None:
    text = APP.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one App.tsx anchor, found {count}: {old[:100]!r}")
    APP.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    'import { GlossaryManager } from "./GlossaryManager";\n',
    'import { GlossaryManager } from "./GlossaryManager";\n'
    'import { GlossaryRecommendations } from "./GlossaryRecommendations";\n',
)
replace_once(
    'import { PreflightPanel } from "./PreflightPanel";\n',
    'import { PreflightPanel } from "./PreflightPanel";\n'
    'import { RuntimeMaintenancePanel } from "./RuntimeMaintenancePanel";\n',
)
replace_once(
    '''          <GlossaryManager\n            apiUrl={API_URL}\n            preset={preset}\n            targetLanguage={glossaryTargetLanguage}\n            disabled={running}\n          />\n''',
    '''          <GlossaryManager\n            apiUrl={API_URL}\n            preset={preset}\n            targetLanguage={glossaryTargetLanguage}\n            disabled={running}\n          />\n          <GlossaryRecommendations preset={preset} disabled={running} />\n''',
)
replace_once(
    '''          {translationProviderUsesModel(translationProvider) && (\n            <label>\n              {translationProvider === "ollama" ? t("engines.translationModel") : t("engines.apiModel")}\n              <input\n                value={translationModel}\n                onChange={(event) => setTranslationModel(event.target.value)}\n                disabled={running}\n              />\n              <small>\n                {translationProvider === "ollama"\n                  ? operatorCopy.translationRecommended\n                  : operatorCopy.exactModelId}\n              </small>\n            </label>\n          )}\n            </div>\n''',
    '''          {translationProviderUsesModel(translationProvider) && (\n            <label>\n              {translationProvider === "ollama" ? t("engines.translationModel") : t("engines.apiModel")}\n              <input\n                value={translationModel}\n                onChange={(event) => setTranslationModel(event.target.value)}\n                disabled={running}\n              />\n              <small>\n                {translationProvider === "ollama"\n                  ? operatorCopy.translationRecommended\n                  : operatorCopy.exactModelId}\n              </small>\n            </label>\n          )}\n          <RuntimeMaintenancePanel disabled={running} />\n            </div>\n''',
)
