(function () {
  "use strict";

  var CACHE_PREFIX = "agentteams_repo_stats_v1:";
  var CACHE_TTL_MS = 30 * 60 * 1000;

  function parseRepoFromHref(href) {
    if (!href) {
      return null;
    }

    try {
      var url = new URL(href, window.location.origin);
      if (!/github\.com$/i.test(url.hostname)) {
        return null;
      }
      var parts = url.pathname.replace(/^\/+|\/+$/g, "").split("/");
      if (parts.length < 2) {
        return null;
      }
      return {
        owner: parts[0],
        repo: parts[1]
      };
    } catch (_err) {
      return null;
    }
  }

  function readCache(cacheKey) {
    try {
      var raw = window.localStorage.getItem(cacheKey);
      if (!raw) {
        return null;
      }
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        return null;
      }
      if (Date.now() - Number(parsed.fetchedAt || 0) > CACHE_TTL_MS) {
        return null;
      }
      if (typeof parsed.stars !== "number" || typeof parsed.forks !== "number") {
        return null;
      }
      return parsed;
    } catch (_err) {
      return null;
    }
  }

  function writeCache(cacheKey, stats) {
    try {
      window.localStorage.setItem(cacheKey, JSON.stringify({
        stars: stats.stars,
        forks: stats.forks,
        fetchedAt: Date.now()
      }));
    } catch (_err) {
      // Ignore cache write failures (private mode/quota).
    }
  }

  function ensureFactItem(container, className, titleText) {
    var item = container.querySelector("." + className);
    if (item) {
      return item;
    }

    item = document.createElement("li");
    item.className = "md-source__fact " + className;
    item.title = titleText;
    item.textContent = "-";
    container.appendChild(item);
    return item;
  }

  function renderStatsOnSource(sourceAnchor, stats) {
    var facts = sourceAnchor.querySelector(".md-source__facts");
    if (!facts) {
      facts = document.createElement("ul");
      facts.className = "md-source__facts";
      sourceAnchor.appendChild(facts);
    }

    var starsItem = ensureFactItem(facts, "md-source__fact--stars", "GitHub stars");
    var forksItem = ensureFactItem(facts, "md-source__fact--forks", "GitHub forks");

    starsItem.textContent = String(stats.stars);
    forksItem.textContent = String(stats.forks);
  }

  function fetchRepoStats(owner, repo) {
    var endpoint = "https://api.github.com/repos/" + owner + "/" + repo;
    return window.fetch(endpoint, {
      headers: {
        "Accept": "application/vnd.github+json"
      }
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("GitHub API request failed: " + response.status);
      }
      return response.json();
    }).then(function (payload) {
      return {
        stars: Number(payload.stargazers_count || 0),
        forks: Number(payload.forks_count || 0)
      };
    });
  }

  function applyRepoStats() {
    var sourceLinks = document.querySelectorAll('a.md-source[data-md-component="source"]');
    if (!sourceLinks.length) {
      return;
    }

    var firstRepo = parseRepoFromHref(sourceLinks[0].getAttribute("href"));
    if (!firstRepo) {
      return;
    }

    var cacheKey = CACHE_PREFIX + firstRepo.owner + "/" + firstRepo.repo;
    var cachedStats = readCache(cacheKey);

    if (cachedStats) {
      sourceLinks.forEach(function (anchor) {
        renderStatsOnSource(anchor, cachedStats);
      });
    }

    fetchRepoStats(firstRepo.owner, firstRepo.repo)
      .then(function (freshStats) {
        writeCache(cacheKey, freshStats);
        sourceLinks.forEach(function (anchor) {
          renderStatsOnSource(anchor, freshStats);
        });
      })
      .catch(function (_err) {
        // Keep existing/cached values if network/API is unavailable.
      });
  }

  function bootstrap() {
    applyRepoStats();

    // Material for MkDocs Instant Navigation emits page events via document$.
    if (typeof window.document$ !== "undefined" && window.document$.subscribe) {
      window.document$.subscribe(function () {
        applyRepoStats();
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
