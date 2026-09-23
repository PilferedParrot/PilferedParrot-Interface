# Live Work events (unreleased source branch)

While a provider works, the browser receives progress and completion events through a
`fetch()` stream. The per-window capability stays in an HTTP header. The stream checks
the window's ownership of each session; it does not put the capability in a URL.

Each event has a sequence number and a server-lifetime epoch. The browser deduplicates
activity after a reconnect. If it has missed the bounded replay buffer, or the server
restarted, it reloads the session's authorized snapshot before continuing. The existing
snapshot poll remains the fallback when a stream is unavailable or disconnected.

This first transport keeps its replay buffer in memory. The chat store remains the durable
session record, and a later event-store migration will retain structured ACP events across
server restarts. The stream does not change provider permissions or the selected model.
