export const LIVE_RECORDING_COPY = {
  ko: {
    title: "녹음 제어", start: "녹음만 시작", stop: "녹음만 종료", refresh: "녹음 상태 새로고침",
    help: "참가자 고지·동의를 확인한 뒤 시작하세요. 녹음을 종료해도 자막과 마이크는 계속됩니다. 녹음하지 않은 구간을 채우거나 추정하지 않습니다.",
    idle: "현재 녹음하지 않습니다.", active: "음성 녹음 중", starting: "녹음 파일을 준비하고 있습니다…",
    stopping: "녹음을 저장하고 있습니다. 자막은 계속됩니다…", failure: "녹음 상태를 확인하거나 변경하지 못했습니다.",
    download: "마지막으로 저장한 녹음 다운로드", offset: "세션 음성에서 시작 위치", unknown: "원래 세션에서 녹음이 시작된 시점을 알 수 없습니다.",
    seek: "이 원문의 녹음 위치로 이동", clock: "입력된 음성의 시간입니다. 발화 단어와 정확히 일치하는 시각은 아닐 수 있습니다.",
  },
  en: {
    title: "Recording controls", start: "Start recording only", stop: "Stop recording only", refresh: "Refresh recording status",
    help: "Confirm participant notice/consent before starting. Stopping recording keeps captions and the microphone running. Unrecorded intervals are never filled or estimated.",
    idle: "Not currently recording.", active: "Recording audio", starting: "Preparing the recording file…",
    stopping: "Saving recording; captions continue…", failure: "Could not read or change recording status.",
    download: "Download last saved recording", offset: "Starts at session audio time", unknown: "The recording's start on the original session clock is unknown.",
    seek: "Seek this source in its recording", clock: "Times follow received audio. Word-level speech timings may be approximate.",
  },
  ja: {
    title: "録音の操作", start: "録音だけ開始", stop: "録音だけ終了", refresh: "録音状態を更新",
    help: "参加者への告知・同意を確認して開始してください。録音を終了しても字幕とマイクは継続します。未録音の区間を補完・推定しません。",
    idle: "現在は録音していません。", active: "音声を録音中", starting: "録音ファイルを準備しています…",
    stopping: "録音を保存中です。字幕は継続します…", failure: "録音状態を確認・変更できませんでした。",
    download: "最後に保存した録音をダウンロード", offset: "セッション音声上の開始位置", unknown: "元のセッション上の録音開始時点は不明です。",
    seek: "この原文の録音位置へ移動", clock: "受信した音声の時刻です。単語ごとの発話位置は正確でない場合があります。",
  },
};
