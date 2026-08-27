import type { AssistantMessage } from "@earendil-works/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const MAX_ATTEMPTS = 3;
const BASE_DELAY_MS = 30_000;
const MAX_DELAY_MS = 120_000;

const TERMINAL_ERROR_PATTERN =
    /aborted|cancel(?:led)?|context (?:length|window)|prompt (?:is )?too long|usage limit|quota|billing|insufficient[_ ]quota|authentication|unauthori[sz]ed|forbidden|invalid api key/i;
const TRANSIENT_ERROR_PATTERN =
    /websocket|network|socket|connection|stream ended without finish_reason|timeout|timed out|overload|rate.?limit|server|service.?unavailable|upstream|bad gateway|gateway timeout|(?:http )?5\d\d/i;

export default function oxAlphaOutageRecovery(pi: ExtensionAPI): void {
    let latestAssistant: AssistantMessage | undefined;
    let attempts = 0;
    let scheduled: ReturnType<typeof setTimeout> | undefined;

    const cancelScheduled = (): void => {
        if (scheduled !== undefined) clearTimeout(scheduled);
        scheduled = undefined;
    };

    const reset = (): void => {
        cancelScheduled();
        latestAssistant = undefined;
        attempts = 0;
    };

    pi.on("session_start", reset);
    pi.on("session_shutdown", reset);
    pi.on("model_select", reset);

    pi.on("input", (event) => {
        if (event.source === "extension") return;
        cancelScheduled();
        if (event.streamingBehavior === undefined) attempts = 0;
    });

    pi.on("message_end", (event) => {
        if (event.message.role === "assistant") latestAssistant = event.message;
    });

    pi.on("agent_settled", (_event, ctx) => {
        const assistant = latestAssistant;
        latestAssistant = undefined;
        if (!assistant || !supportsBackgroundRecovery(ctx)) return;

        if (!shouldRecoverOxAlphaFailure(assistant)) {
            attempts = 0;
            return;
        }

        if (attempts >= MAX_ATTEMPTS) {
            notify(ctx, `Ox Alpha recovery stopped after ${attempts} attempts.`);
            return;
        }

        attempts += 1;
        const delayMs = Math.min(BASE_DELAY_MS * 2 ** (attempts - 1), MAX_DELAY_MS);
        notify(
            ctx,
            `Transient Ox Alpha failure; recovery ${attempts}/${MAX_ATTEMPTS} will resume in ${formatDelay(delayMs)}.`,
        );
        cancelScheduled();
        scheduled = setTimeout(() => {
            scheduled = undefined;
            if (!ctx.isIdle()) return;
            pi.sendUserMessage(
                "Continue the interrupted task from the current session and filesystem state. Do not repeat completed work. Verify partial work before changing it.",
            );
        }, delayMs);
    });
}

export function shouldRecoverOxAlphaFailure(message: AssistantMessage): boolean {
    if (message.stopReason !== "error" || !message.errorMessage) return false;
    if (message.provider.trim().toLowerCase() !== "opencode-go") return false;
    if (!message.model.trim().toLowerCase().startsWith("ox-alpha")) return false;
    if (TERMINAL_ERROR_PATTERN.test(message.errorMessage)) return false;
    return TRANSIENT_ERROR_PATTERN.test(message.errorMessage);
}

function supportsBackgroundRecovery(ctx: ExtensionContext): boolean {
    return ctx.mode === "tui" || ctx.mode === "rpc";
}

function notify(ctx: ExtensionContext, message: string): void {
    if (ctx.hasUI) ctx.ui.notify(message, "warning");
}

function formatDelay(delayMs: number): string {
    return `${Math.ceil(delayMs / 1000)}s`;
}
