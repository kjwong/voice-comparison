(function () {
  "use strict";

  var state = {
    voices: null,
    currentAudio: null,
    currentBtn: null,
    genderFilter: "all",
    starred: JSON.parse(localStorage.getItem("starred") || "[]"),
    customText: "",
    proxyUrl: "",
    proxyKey: "",
  };

  var SAMPLE_TYPES = ["conversational", "formal", "emotional"];

  function init() {
    fetch("voices.json")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        state.voices = data;
        renderCards("candidates-grid", data.candidates, false);
        renderCards("current-grid", data.current, true);
        bindFilterButtons();
        bindCustomInput();
        bindGenerateStarred();
      });
  }

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
          " " + st.charAt(0).toUpperCase() + st.slice(1)
        ));
        btn.addEventListener("click", function () { playAudio(btn); });
        playBtns.appendChild(btn);
      });

      container.appendChild(clone);
    });
  }

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
      " " + type.charAt(0).toUpperCase() + type.slice(1)
    ));
  }

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
    updateGenerateStarredBtn();
  }

  function bindCustomInput() {
    var ta = document.getElementById("custom-text");
    ta.addEventListener("input", function () {
      state.customText = ta.value.trim();
      toggleGenerateButtons(state.customText.length > 0);
      updateGenerateStarredBtn();
    });
  }

  function toggleGenerateButtons(show) {
    document.querySelectorAll(".voice-card").forEach(function (card) {
      var existing = card.querySelector(".generate-btn");
      var playBtns = card.querySelector(".play-buttons");
      if (show && !existing) {
        var btn = document.createElement("button");
        btn.className = "generate-btn";
        btn.textContent = "Generate";
        btn.addEventListener("click", function () {
          generateForVoice(card, btn);
        });
        playBtns.appendChild(btn);
      } else if (!show && existing) {
        existing.remove();
        var dynBtn = card.querySelector('.play-btn[data-type="custom"]');
        if (dynBtn) dynBtn.remove();
      }
    });
  }

  function generateForVoice(card, genBtn) {
    if (!state.proxyUrl || !state.customText) return;
    var key = card.dataset.key;
    var all = state.voices.candidates.concat(state.voices.current);
    var voice = null;
    for (var i = 0; i < all.length; i++) {
      if (all[i].key === key) { voice = all[i]; break; }
    }
    if (!voice) return;

    genBtn.classList.add("loading");
    genBtn.classList.remove("failed");
    genBtn.textContent = "Generating...";

    fetch(state.proxyUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-proxy-key": state.proxyKey,
      },
      body: JSON.stringify({
        provider: voice.provider,
        voice_id: voice.voiceId,
        text: state.customText,
        model: voice.model,
      }),
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var dynBtn = card.querySelector('.play-btn[data-type="custom"]');
        if (!dynBtn) {
          dynBtn = document.createElement("button");
          dynBtn.className = "play-btn";
          dynBtn.dataset.voice = key;
          dynBtn.dataset.type = "custom";
          var icon = document.createElement("span");
          icon.className = "icon";
          icon.textContent = "\u25B6";
          dynBtn.appendChild(icon);
          dynBtn.appendChild(document.createTextNode(" Custom"));
          dynBtn.addEventListener("click", function () { playAudio(dynBtn); });
          card.querySelector(".play-buttons").insertBefore(dynBtn, genBtn);
        }
        dynBtn.dataset.src = url;
        genBtn.classList.remove("loading");
        genBtn.textContent = "Generate";
        playAudio(dynBtn);
      })
      .catch(function (err) {
        genBtn.classList.remove("loading");
        genBtn.classList.add("failed");
        genBtn.textContent = "Failed";
        console.error("Generate failed for " + key + ":", err);
      });
  }

  function bindGenerateStarred() {
    document.getElementById("generate-starred")
      .addEventListener("click", function () {
        if (!state.proxyUrl || !state.customText ||
            state.starred.length === 0) return;
        state.starred.forEach(function (key) {
          var card = document.querySelector(
            '.voice-card[data-key="' + CSS.escape(key) + '"]'
          );
          var genBtn = card && card.querySelector(".generate-btn");
          if (card && genBtn) generateForVoice(card, genBtn);
        });
      });
  }

  function updateGenerateStarredBtn() {
    var btn = document.getElementById("generate-starred");
    var n = state.starred.length;
    btn.disabled = !state.customText || n === 0 || !state.proxyUrl;
    btn.textContent = n > 0
      ? "Generate for " + n + " starred"
      : "Generate for starred";
  }

  document.addEventListener("DOMContentLoaded", init);
})();
