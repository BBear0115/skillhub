# Server Transfer Skill

Use this skill when the caller needs to stream audio chunks or text chunks into server-side storage, or delete one stream or many streams in one request.

## Tools

- `stream_audio_to_server`
  - Send one base64-encoded audio chunk per call.
  - Set `finalize=true` on the last call to assemble the server-side binary artifact.

- `stream_text_to_server`
  - Send one text chunk per call.
  - Set `finalize=true` on the last call to refresh the assembled text file.

- `delete_server_streams`
  - Use `mode="stream"` with one `stream_id` to delete a single stream.
  - Use `mode="batch"` with `stream_ids` to delete many streams in one request.

## Storage behavior

The tool writes into server-side storage grouped by `skill_id` and `skill_version_id`. Every stream keeps:

- `manifest.json`
- `chunks/`
- `assembled.*` output when applicable

This makes the tool suitable for review, staging, and controlled batch cleanup work.
