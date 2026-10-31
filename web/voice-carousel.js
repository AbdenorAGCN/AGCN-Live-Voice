(()=>{"use strict";
const gallery=document.querySelector(".agcn-home-gallery .agcn-showcase");if(!gallery)return;
const images=Array.from(gallery.querySelectorAll(".showcase-cover"));if(images.length<2)return;
const reduced=window.matchMedia("(prefers-reduced-motion: reduce)");let position=0,timer=0;
function scale(){images.forEach(c=>c.style.setProperty("--ui-scale",String(Math.max(1,c.offsetWidth)/1600)))}
if(typeof ResizeObserver!=="undefined")new ResizeObserver(scale).observe(gallery);
window.addEventListener("resize",scale);
function distance(i){let d=(i-position+images.length)%images.length;if(d>Math.floor(images.length/2))d-=images.length;return d}
function paint(){images.forEach((frame,i)=>{const d=distance(i);let slot=d===0?"center":d===-1?"previous":d===1?"next":d===-2?"previous-far":d===2?"next-far":d<0?"off-left":"off-right";frame.dataset.flowSlot=slot;frame.setAttribute("aria-hidden",slot==="center"?"false":"true")})}
function move(){position=(position+1)%images.length;paint()}
function run(){return !document.hidden&&!reduced.matches}
function restart(){if(timer){clearInterval(timer);timer=0}if(run())timer=setInterval(()=>{if(run())move()},2000)}
paint();scale();requestAnimationFrame(()=>requestAnimationFrame(()=>gallery.classList.add("agcn-flow-ready")));
document.addEventListener("visibilitychange",restart);window.addEventListener("pagehide",()=>{if(timer)clearInterval(timer)});
reduced.addEventListener?.("change",restart);restart();
})();