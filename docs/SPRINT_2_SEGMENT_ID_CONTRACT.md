# Sprint 2 segment ID contract

`final_transcript.json` schema version 2 persists one `seg_v1_<sha256>` ID per
segment. The hash is over canonical start and end centiseconds only, using
`ROUND_HALF_UP`; text, speaker, ordering, and diarization never affect it.

Version-2 files must declare the v1 scheme/version and contain matching IDs.
Older files remain readable: their IDs are derived in memory from their parsed
time ranges and the source file is never rewritten. Duplicate canonical time
ranges are invalid in both formats.
