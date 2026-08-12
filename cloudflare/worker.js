/**
 * ╔══════════════════════════════════════════════════════════════════╗
 * ║  INSTITUTIONAL HUNTER PRO — WORKER CLOUDFLARE                    ║
 * ║                                                                   ║
 * ║  Ce Worker resout les DEUX causes du manque de signaux :          ║
 * ║                                                                   ║
 * ║  1. CADENCE — GitHub etrangle les crons `schedule` (runs espaces  ║
 * ║     de 1 a 3 h dans les faits). Les Cron Triggers de Cloudflare,  ║
 * ║     eux, sont ponctuels. Le Worker declenche le workflow via      ║
 * ║     `workflow_dispatch`, qui n'est PAS soumis a cet etranglement. ║
 * ║                                                                   ║
 * ║  2. COUVERTURE EXCHANGES — Binance et Kraken bloquent souvent les ║
 * ║     IP des runners GitHub, d'ou les "4 exchanges sur 6" de tes    ║
 * ║     alertes. Le Worker sert de relais : les requetes partent de   ║
 * ║     l'infrastructure Cloudflare, qui passe.                       ║
 * ║                                                                   ║
 * ║  Le relais ne touche JAMAIS aux endpoints MEXC signes : les       ║
 * ║  requetes authentifiees restent en direct depuis le runner.       ║
 * ╚══════════════════════════════════════════════════════════════════╝
 */

// Domaines autorises pour le relais. Uniquement des endpoints PUBLICS
// de carnet d'ordres. Aucun domaine MEXC : les requetes signees ne
// doivent jamais transiter par un tiers.
const DOMAINES_AUTORISES = [
  "api.binance.com",
  "data-api.binance.vision",
  "api.kraken.com",
  "api.bybit.com",
  "www.okx.com",
  "api.bitget.com",
];

const CACHE_SECONDES = 3; // les carnets bougent vite, cache tres court

/** Declenche le workflow GitHub via workflow_dispatch. */
async function declencherWorkflow(env) {
  const url =
    `https://api.github.com/repos/${env.GH_REPO}` +
    `/actions/workflows/${env.GH_WORKFLOW}/dispatches`;

  const reponse = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      "User-Agent": "ihp-scheduler",
    },
    body: JSON.stringify({ ref: env.GH_BRANCH || "main" }),
  });

  // 204 = accepte. Tout le reste merite d'etre lu.
  if (reponse.status !== 204) {
    const texte = await reponse.text();
    console.error(`Dispatch echoue (${reponse.status}) : ${texte}`);
    return { ok: false, status: reponse.status, detail: texte };
  }
  console.log("Workflow declenche.");
  return { ok: true, status: 204 };
}

/** Relais pour les API publiques de carnet d'ordres. */
async function relayer(request, env) {
  // Jeton obligatoire : sans lui, ce Worker serait un proxy ouvert
  // que n'importe qui pourrait utiliser a ta place.
  if (env.PROXY_TOKEN) {
    const fourni = request.headers.get("X-Proxy-Token");
    if (fourni !== env.PROXY_TOKEN) {
      return new Response("Jeton invalide", { status: 403 });
    }
  }

  const cible = new URL(request.url).searchParams.get("url");
  if (!cible) return new Response("Parametre `url` manquant", { status: 400 });

  let destination;
  try {
    destination = new URL(cible);
  } catch {
    return new Response("URL invalide", { status: 400 });
  }

  if (destination.protocol !== "https:") {
    return new Response("HTTPS uniquement", { status: 400 });
  }
  if (!DOMAINES_AUTORISES.includes(destination.hostname)) {
    return new Response(`Domaine non autorise : ${destination.hostname}`, {
      status: 403,
    });
  }

  const amont = await fetch(destination.toString(), {
    headers: { "User-Agent": "ihp-relay/1.0", Accept: "application/json" },
    cf: { cacheTtl: CACHE_SECONDES, cacheEverything: true },
  });

  return new Response(amont.body, {
    status: amont.status,
    headers: {
      "Content-Type": amont.headers.get("Content-Type") || "application/json",
      "Cache-Control": `public, max-age=${CACHE_SECONDES}`,
    },
  });
}

export default {
  /** Cron Trigger — voir `crons` dans wrangler.toml. */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(declencherWorkflow(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/proxy") {
      return relayer(request, env);
    }

    // Declenchement manuel, pratique pour tester l'installation.
    if (url.pathname === "/run") {
      if (env.PROXY_TOKEN &&
          request.headers.get("X-Proxy-Token") !== env.PROXY_TOKEN) {
        return new Response("Jeton invalide", { status: 403 });
      }
      const r = await declencherWorkflow(env);
      return new Response(JSON.stringify(r, null, 2), {
        status: r.ok ? 200 : 502,
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response(
      "IHP Worker actif.\n" +
      "  GET /proxy?url=<url_exchange>   relais carnet d'ordres\n" +
      "  GET /run                        declenche le workflow\n",
      { headers: { "Content-Type": "text/plain; charset=utf-8" } }
    );
  },
};
