import { v } from "convex/values";
import { internalMutation, internalQuery } from "./_generated/server";

export const ingest = internalMutation({
  args: { kind: v.string(), payload: v.any() },
  handler: async (ctx, { kind, payload }) => {
    await ctx.db.insert("events", { kind, payload, at: Date.now() });
  },
});

export const latestVideo = internalQuery({
  args: {},
  handler: async (ctx) => {
    const video = await ctx.db.query("events").withIndex("by_at").order("desc")
      .filter((q) => q.eq(q.field("kind"), "video")).first();
    return video ? await ctx.storage.getUrl(video.payload.storageId) : null;
  },
});

// The latest latency sweep, and for each phase its latest reset plus the rounds published after it.
export const state = internalQuery({
  args: {},
  handler: async (ctx) => {
    const events = (await ctx.db.query("events").withIndex("by_at").order("desc").take(200)).reverse();
    const latency = events.filter((e) => e.kind === "latency").at(-1)?.payload ?? null;
    const phases: Record<string, { reset: any; rounds: any[] }> = {};
    let current: string | null = null;
    for (const e of events) {
      if (e.kind === "reset") {
        current = e.payload.phase ?? "train";
        phases[current] = { reset: e.payload, rounds: [] };
      } else if (e.kind === "round" && current && (e.payload.phase ?? "train") === current) {
        phases[current].rounds.push(e.payload);
      }
    }
    return { latency, train: phases.train ?? null, holdout: phases.holdout ?? null };
  },
});
