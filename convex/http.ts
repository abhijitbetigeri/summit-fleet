import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { internal } from "./_generated/api";
import { PAGE } from "./page";

const http = httpRouter();

http.route({
  path: "/ingest",
  method: "POST",
  handler: httpAction(async (ctx, req) => {
    const secret = process.env.FLEET_SECRET;
    if (secret && req.headers.get("x-fleet-secret") !== secret) {
      return new Response("forbidden", { status: 403 });
    }
    const { kind, payload } = await req.json();
    await ctx.runMutation(internal.fleet.ingest, { kind, payload });
    return new Response("ok");
  }),
});

http.route({
  path: "/upload-video",
  method: "POST",
  handler: httpAction(async (ctx, req) => {
    if (req.headers.get("x-fleet-secret") !== process.env.FLEET_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    const storageId = await ctx.storage.store(await req.blob());
    await ctx.runMutation(internal.fleet.ingest, { kind: "video", payload: { storageId } });
    return new Response("ok");
  }),
});

http.route({
  path: "/video.mp4",
  method: "GET",
  handler: httpAction(async (ctx) => {
    const url = await ctx.runQuery(internal.fleet.latestVideo, {});
    return url ? Response.redirect(url, 302) : new Response("no video yet", { status: 404 });
  }),
});

http.route({
  path: "/state",
  method: "GET",
  handler: httpAction(async (ctx) => {
    const state = await ctx.runQuery(internal.fleet.state, {});
    return new Response(JSON.stringify(state), {
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  }),
});

http.route({
  path: "/",
  method: "GET",
  handler: httpAction(async () => new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8" } })),
});

export default http;
