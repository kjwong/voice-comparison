(function () {
  "use strict";

  var state = {
    voices: null,
    currentAudio: null,
    currentBtn: null,
    genderFilter: "all",
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
        renderCards("candidates-grid", data.candidates, false);
        renderCards("current-grid", data.current, true);
        populateVoiceSelect();
        bindFilterButtons();
        bindCustomInput();
        bindGenerate();
      });
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

  // --- Gender Filter ---
  function bindFilterButtons() {
    var btns = document.querySelectorAll(".filter-btn");
    btns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        btns.forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        state.genderFilter = btn.dataset.filter;
        applyFilter();
      });
    });
  }

  function applyFilter() {
    document.querySelectorAll(".voice-card").forEach(function (card) {
      if (state.genderFilter === "all" ||
          card.dataset.gender === state.genderFilter) {
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
