const background = document.getElementById("background");

function setBackground(value) {
  if (typeof value !== "string" || !/^assets\/[a-f0-9]{20}\.(png|jpe?g|webp)$/.test(value)) {
    if (value !== "assets/BG_Final.png") return;
  }
  if (background.getAttribute("src") === value) return;
  background.src = value;
}

function livelyPropertyListener(name, value) {
  if (name === "echoesBackground") setBackground(value);
}

setBackground("assets/BG_Final.png");
