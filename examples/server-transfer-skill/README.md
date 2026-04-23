Zip the contents of this folder, keeping `skill.json` at the root of the archive, then upload it to SkillHub.

This example provides three tools:

- `stream_audio_to_server`
- `stream_text_to_server`
- `delete_server_streams`

Suggested verification flow:

1. Upload the ZIP into a workspace.
2. Have the super admin start review, download the ZIP, and inspect the prepared workbench.
3. Approve the version.
4. Enable the skill in a team workspace.
5. Call the text and audio tools with multiple chunks.
6. Use the delete tool in `stream` mode or `batch` mode to remove stored streams.
