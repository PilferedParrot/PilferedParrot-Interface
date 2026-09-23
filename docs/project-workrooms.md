# Project workrooms (unreleased source branch)

The **Project** selector at the top of Work's sidebar chooses the folder for new sessions and
filters the session list to that project. The star pins a folder above recent projects; **＋**
lets you choose another folder and starts a session there. The selected folder, recent list,
and pins survive closing and reopening the app.

Opening the app again resumes the most recently used session in its selected project. Use
**New Session** when you want a separate conversation. A session with messages keeps its
original folder; selecting another project does not move its files or transcript. Drafts
remain with their sessions when you switch projects.

Provider windows keep their own session history and project choices. The main dashboard can
show project session counts across provider windows, but it does not expose another window's
messages or session IDs. To work in the selected project with another provider, open
**Providers** and launch that provider from the project folder.

Project roots are resolved before use. The existing provider workspace validation still
applies, including restrictions on a whole-home workspace and configured additional roots.
A missing or unwritable folder must be repaired or replaced before starting a new session in
it. Pinned and recent entries are local preferences stored beside the chat history.
