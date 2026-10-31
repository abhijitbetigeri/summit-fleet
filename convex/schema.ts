import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  events: defineTable({ kind: v.string(), payload: v.any(), at: v.number() }).index("by_at", ["at"]),
});
