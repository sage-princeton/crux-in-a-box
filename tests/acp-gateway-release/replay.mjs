// Drives the installed gateway's requestPermission through one permission
// request against a stand-in workspace, and prints how it ended as JSON.
//
//   early-verdict  the workspace answers before acknowledging the request,
//                  with the timings seen on crux-web-pilot3 (verdict at
//                  ~280 ms, acknowledgement at ~350 ms)
//   send-fails     the request never reaches the workspace
import { EventEmitter } from "node:events";

const [modulePath, scenario] = process.argv.slice(2);
const { AgentRQACPClient } = await import(modulePath);

const TIMEOUT_MS = 1000;
const bridge = Object.assign(new EventEmitter(), {
  getSessionId: () => "replay",
  callTool: async () => {},
  sendNotification: (_method, payload) => {
    if (scenario === "send-fails") return Promise.reject(new Error("workspace unreachable"));
    setTimeout(() => bridge.emit("verdict", { requestId: payload.request_id, behavior: "allow" }), 280);
    return new Promise((resolve) => setTimeout(resolve, 350));
  },
});
const client = new AgentRQACPClient(bridge, () => "task-1", { permissionTimeoutMs: TIMEOUT_MS });
let turnCancelled = false;
client.setSessionCanceller(() => {
  turnCancelled = true;
});

const response = await client.requestPermission({
  sessionId: "sess-1",
  toolCall: { toolCallId: "call-1", title: "Run command", rawInput: { command: "ls" } },
  options: [
    { optionId: "allow", kind: "allow_once", name: "Allow" },
    { optionId: "deny", kind: "reject_once", name: "Deny" },
  ],
});
// Outlive the timeout, so a timer the request left behind gets to fire.
await new Promise((resolve) => setTimeout(resolve, TIMEOUT_MS + 200));
process.stdout.write(JSON.stringify({ outcome: response.outcome, turnCancelled }) + "\n");
