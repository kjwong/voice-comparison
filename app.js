(function () {
  "use strict";

  var state = {
    voices: null,
    currentAudio: null,
    currentBtn: null,
    genderFilter: "all",
    providerFilter: "all",
    activeRound: null,
    starred: JSON.parse(localStorage.getItem("starred") || "[]"),
    proxyUrl: "",
    proxyKey: "",
  };

  var SAMPLE_TYPES = ["sample1", "sample2", "sample3", "sample1_slow", "sample1_fast"];
  var SAMPLE_LABELS = {
    sample1: "Audio 1", sample2: "Audio 2", sample3: "Audio 3",
    sample1_slow: "Audio 1 Slow", sample1_fast: "Audio 1 Fast"
  };

  // --- Init ---
  function init() {
    fetch("voices.json")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.voices = data;
        var rounds = collectRounds(data.candidates);
        state.activeRound = rounds[0] || null;
        renderRoundTabs(rounds);
        renderCandidates();
        renderCards("current-grid", data.current, true);
        populateVoiceSelect();
        populateProviderFilters(data);
        bindFilterButtons();
        bindCustomInput();
        bindGenerate();
      });
  }

  // --- Round Tabs ---
  function collectRounds(candidates) {
    var seen = {};
    candidates.forEach(function (v) {
      (v.rounds || []).forEach(function (r) { seen[r] = true; });
    });
    return Object.keys(seen).sort().reverse();
  }

  function renderRoundTabs(rounds) {
    var container = document.getElementById("round-tabs");
    if (!container) return;
    container.replaceChildren();
    rounds.forEach(function (r) {
      var btn = document.createElement("button");
      btn.className = "round-tab" + (r === state.activeRound ? " active" : "");
      btn.dataset.round = r;
      btn.textContent = r;
      btn.addEventListener("click", function () {
        if (state.activeRound === r) return;
        state.activeRound = r;
        document.querySelectorAll(".round-tab").forEach(function (b) {
          b.classList.toggle("active", b.dataset.round === r);
        });
        renderCandidates();
      });
      container.appendChild(btn);
    });
  }

  function renderCandidates() {
    ["latest-grid", "will-replace-grid", "considered-grid", "candidates-grid"]
      .forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.replaceChildren();
      });

    var rounds = collectRounds(state.voices.candidates);
    var newest = rounds[0];
    var inRound = state.voices.candidates.filter(function (v) {
      return (v.rounds || []).indexOf(state.activeRound) >= 0;
    });

    var groups = {
      latest:     { ids: ["latest-section",       "latest-grid"],       items: [] },
      willReplace:{ ids: ["will-replace-section", "will-replace-grid"], items: [] },
      considered: { ids: ["considered-section",   "considered-grid"],   items: [] },
      rest:       { ids: ["candidates-section",   "candidates-grid"],   items: [] },
    };
    if (state.activeRound === newest) {
      inRound.forEach(function (v) {
        if (v.latest) groups.latest.items.push(v);
        else if (v.willReplace) groups.willReplace.items.push(v);
        else if (v.consideredLastTime) groups.considered.items.push(v);
        else groups.rest.items.push(v);
      });
    } else {
      groups.rest.items = inRound;
    }

    Object.keys(groups).forEach(function (k) {
      var g = groups[k];
      var section = document.getElementById(g.ids[0]);
      if (g.items.length) {
        if (section) section.style.display = "";
        renderCards(g.ids[1], g.items, false);
      } else if (section) {
        section.style.display = "none";
      }
    });
    applyFilter();
  }

  // --- Voice Select Dropdown ---
  function populateVoiceSelect() {
    var select = document.getElementById("voice-select");
    var all = state.voices.candidates.concat(state.voices.current);

    var groups = {};
    all.forEach(function (v) {
      var label = v.provider;
      if (!groups[label]) groups[label] = [];
      groups[label].push(v);
    });

    Object.keys(groups).forEach(function (provider) {
      var optgroup = document.createElement("optgroup");
      optgroup.label = provider;
      groups[provider].forEach(function (v) {
        var opt = document.createElement("option");
        opt.value = v.key;
        opt.textContent = v.name + " (" + v.accent + " " + v.gender + ")";
        optgroup.appendChild(opt);
      });
      select.appendChild(optgroup);
    });
  }

  // --- Card Rendering ---
  function renderCards(containerId, voices, isCurrent) {
    var container = document.getElementById(containerId);
    var template = document.getElementById("card-template");
    voices.forEach(function (voice) {
      var clone = template.content.cloneNode(true);
      var card = clone.querySelector(".voice-card");
      if (isCurrent) card.classList.add("current-voice");
      card.dataset.gender = voice.gender;
      card.dataset.key = voice.key;
      card.dataset.provider = voice.provider;

      card.querySelector(".voice-name").textContent = voice.name;
      card.querySelector(".provider-label").textContent =
        voice.provider + " \u00B7 " + voice.model;

      var starBtn = card.querySelector(".star-btn");
      if (state.starred.indexOf(voice.key) >= 0) starBtn.classList.add("starred");
      starBtn.addEventListener("click", function () {
        toggleStar(starBtn, voice.key);
      });

      var badges = card.querySelector(".badges");
      var accentBadge = document.createElement("span");
      accentBadge.className = "badge badge-accent-" + voice.accent.toLowerCase();
      accentBadge.textContent = voice.accent;
      badges.appendChild(accentBadge);

      var genderBadge = document.createElement("span");
      genderBadge.className = "badge badge-gender-" + voice.gender.toLowerCase();
      genderBadge.textContent = voice.gender === "M" ? "Male" : "Female";
      badges.appendChild(genderBadge);

      card.querySelector(".stat-cost").textContent = voice.costPer1M + "/1M";
      card.querySelector(".stat-latency").textContent = voice.latency;
      card.querySelector(".stat-speed").textContent = voice.speedControl;

      var playBtns = card.querySelector(".play-buttons");
      SAMPLE_TYPES.forEach(function (st) {
        var btn = document.createElement("button");
        btn.className = "play-btn";
        btn.dataset.voice = voice.key;
        btn.dataset.type = st;
        btn.dataset.src = voice.audioPath + "/" + st + ".mp3";
        var icon = document.createElement("span");
        icon.className = "icon";
        icon.textContent = "\u25B6";
        btn.appendChild(icon);
        btn.appendChild(document.createTextNode(
          " " + SAMPLE_LABELS[st]
        ));
        btn.addEventListener("click", function () { playAudio(btn); });
        playBtns.appendChild(btn);
      });

      container.appendChild(clone);
    });
  }

  // --- Audio Playback ---
  function playAudio(btn) {
    var src = btn.dataset.src;
    var card = btn.closest(".voice-card");
    var progressContainer = card.querySelector(".audio-progress");
    var progressBar = card.querySelector(".audio-progress-bar");

    if (state.currentBtn === btn && state.currentAudio &&
        !state.currentAudio.paused) {
      stopAudio();
      return;
    }
    stopAudio();

    var audio = new Audio(src);
    state.currentAudio = audio;
    state.currentBtn = btn;
    btn.classList.add("playing");
    progressContainer.classList.add("visible");

    audio.addEventListener("timeupdate", function () {
      if (audio.duration) {
        progressBar.style.width =
          ((audio.currentTime / audio.duration) * 100) + "%";
      }
    });
    audio.addEventListener("ended", stopAudio);
    audio.addEventListener("error", function () {
      stopAudio();
      btn.textContent = "Error";
      setTimeout(function () { resetBtnLabel(btn); }, 2000);
    });
    audio.play();
  }

  function playAudioFromUrl(url) {
    stopAudio();
    var audio = new Audio(url);
    state.currentAudio = audio;
    state.currentBtn = null;
    audio.addEventListener("ended", stopAudio);
    audio.play();
  }

  function stopAudio() {
    if (state.currentAudio) {
      state.currentAudio.pause();
      state.currentAudio.currentTime = 0;
      state.currentAudio = null;
    }
    if (state.currentBtn) {
      state.currentBtn.classList.remove("playing");
      var card = state.currentBtn.closest(".voice-card");
      if (card) {
        var p = card.querySelector(".audio-progress");
        var b = card.querySelector(".audio-progress-bar");
        if (p) p.classList.remove("visible");
        if (b) b.style.width = "0%";
      }
      state.currentBtn = null;
    }
  }

  function resetBtnLabel(btn) {
    var type = btn.dataset.type;
    btn.textContent = "";
    var icon = document.createElement("span");
    icon.className = "icon";
    icon.textContent = "\u25B6";
    btn.appendChild(icon);
    btn.appendChild(document.createTextNode(
      " " + (SAMPLE_LABELS[type] || type)
    ));
  }

  // --- Provider Filter ---
  function populateProviderFilters(data) {
    var container = document.getElementById("provider-filters");
    var providers = [];
    var seen = {};
    data.candidates.concat(data.current).forEach(function (v) {
      if (!seen[v.provider]) {
        seen[v.provider] = true;
        providers.push(v.provider);
      }
    });

    var allBtn = document.createElement("button");
    allBtn.className = "filter-btn provider-btn active";
    allBtn.dataset.provider = "all";
    allBtn.textContent = "All providers";
    container.appendChild(allBtn);

    providers.forEach(function (p) {
      var btn = document.createElement("button");
      btn.className = "filter-btn provider-btn";
      btn.dataset.provider = p;
      btn.textContent = p;
      container.appendChild(btn);
    });
  }

  // --- Filters ---
  function bindFilterButtons() {
    document.addEventListener("click", function (e) {
      var btn = e.target;
      if (btn.classList.contains("gender-btn")) {
        document.querySelectorAll(".gender-btn").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        state.genderFilter = btn.dataset.filter;
        applyFilter();
      } else if (btn.classList.contains("provider-btn")) {
        document.querySelectorAll(".provider-btn").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        state.providerFilter = btn.dataset.provider;
        applyFilter();
      }
    });
  }

  function applyFilter() {
    document.querySelectorAll(".voice-card").forEach(function (card) {
      var genderMatch = state.genderFilter === "all" || card.dataset.gender === state.genderFilter;
      var providerMatch = state.providerFilter === "all" || card.dataset.provider === state.providerFilter;
      if (genderMatch && providerMatch) {
        card.classList.remove("hidden");
      } else {
        card.classList.add("hidden");
      }
    });
  }

  // --- Favorites ---
  function toggleStar(btn, key) {
    var idx = state.starred.indexOf(key);
    if (idx >= 0) {
      state.starred.splice(idx, 1);
      btn.classList.remove("starred");
    } else {
      state.starred.push(key);
      btn.classList.add("starred");
    }
    localStorage.setItem("starred", JSON.stringify(state.starred));
  }

  // --- Custom Input + Generate ---
  function bindCustomInput() {
    var ta = document.getElementById("custom-text");
    ta.addEventListener("input", function () {
      updateGenerateBtn();
    });
    document.getElementById("voice-select").addEventListener("change", updateGenerateBtn);
  }

  function updateGenerateBtn() {
    var btn = document.getElementById("generate-btn");
    var text = document.getElementById("custom-text").value.trim();
    var voice = document.getElementById("voice-select").value;
    btn.disabled = !text || !voice || !state.proxyUrl;
  }

  function bindGenerate() {
    document.getElementById("generate-btn").addEventListener("click", function () {
      var btn = document.getElementById("generate-btn");
      var text = document.getElementById("custom-text").value.trim();
      var voiceKey = document.getElementById("voice-select").value;
      var speed = parseFloat(document.getElementById("speed-select").value);

      if (!text || !voiceKey || !state.proxyUrl) return;

      var all = state.voices.candidates.concat(state.voices.current);
      var voice = null;
      for (var i = 0; i < all.length; i++) {
        if (all[i].key === voiceKey) { voice = all[i]; break; }
      }
      if (!voice) return;

      btn.disabled = true;
      btn.textContent = "Generating...";

      fetch(state.proxyUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-proxy-key": state.proxyKey,
        },
        body: JSON.stringify({
          provider: voice.provider,
          voice_id: voice.voiceId,
          text: text,
          model: voice.model,
          speed: speed,
        }),
      })
        .then(function (resp) {
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          return resp.blob();
        })
        .then(function (blob) {
          var url = URL.createObjectURL(blob);
          btn.disabled = false;
          btn.textContent = "Generate";
          playAudioFromUrl(url);
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = "Failed — retry?";
          console.error("Generate failed:", err);
          setTimeout(function () { btn.textContent = "Generate"; }, 3000);
        });
    });
  }

  // --- Boot ---
  document.addEventListener("DOMContentLoaded", init);
})();
