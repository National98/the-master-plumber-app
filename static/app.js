(function(){
 const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
 // Agreement signature
 if($('#agreementForm')){
   const c=$('#sig'), ctx=c.getContext('2d'); ctx.lineWidth=2.5; ctx.lineCap='round'; ctx.strokeStyle='#092742'; let draw=false, hasInk=false;
   function pt(e){const r=c.getBoundingClientRect(), t=e.touches?e.touches[0]:e; return [(t.clientX-r.left)*(c.width/r.width),(t.clientY-r.top)*(c.height/r.height)]}
   function start(e){draw=true;hasInk=true;const [x,y]=pt(e);ctx.beginPath();ctx.moveTo(x,y);e.preventDefault()}
   function move(e){if(!draw)return;const[x,y]=pt(e);ctx.lineTo(x,y);ctx.stroke();e.preventDefault()}
   function end(){draw=false}
   ['mousedown','touchstart'].forEach(x=>c.addEventListener(x,start,{passive:false})); ['mousemove','touchmove'].forEach(x=>c.addEventListener(x,move,{passive:false})); ['mouseup','mouseleave','touchend'].forEach(x=>c.addEventListener(x,end));
   $('#clearSig').onclick=()=>{ctx.clearRect(0,0,c.width,c.height);hasInk=false};
   $('#agreementForm').addEventListener('submit',(e)=>{const existing=$('#agreementForm').dataset.hasSignature==='1'; if(!hasInk&&!existing){e.preventDefault();alert('Please have the customer sign in the signature box before saving.');c.scrollIntoView({behavior:'smooth',block:'center'});return} if(hasInk)$('#signatureData').value=c.toDataURL('image/png')});
 }
 // Assessment performed
 if($('#assessmentForm')){const ex=window.existingPerformed||[]; $$('#performedChecks input').forEach(i=>{if(ex.includes(i.value))i.checked=true}); const sync=()=>$('#performedValue').value=$$('#performedChecks input:checked').map(i=>i.value).join('|'); sync(); $$('#performedChecks input').forEach(i=>i.addEventListener('change',sync)); $('#assessmentForm').addEventListener('submit',sync)}
 // Invoice builder
 if($('#invoiceForm')){
   const box=$('#invoiceItems'), tpl=$('#invoiceRowTpl');
   function wire(row){const sel=row.querySelector('.pricing-select'), desc=row.querySelector('[name=descriptions]'), price=row.querySelector('[name=unit_prices]'), qty=row.querySelector('[name=quantities]'); if(sel)sel.addEventListener('change',()=>{const o=sel.selectedOptions[0]; if(o&&o.value){desc.value=o.dataset.name||'';price.value=o.dataset.price||0}calc()}); [price,qty].forEach(x=>x&&x.addEventListener('input',calc)); row.querySelector('.remove-row').onclick=()=>{row.remove();calc()}}
   function add(pid='',name='',price=0){const n=tpl.content.firstElementChild.cloneNode(true);box.appendChild(n);wire(n); if(pid){n.querySelector('.pricing-select').value=pid;n.querySelector('[name=descriptions]').value=name;n.querySelector('[name=unit_prices]').value=price}calc()}
   function calc(){let t=0; $$('.invoice-row').forEach(r=>{t+=(parseFloat(r.querySelector('[name=quantities]')?.value)||0)*(parseFloat(r.querySelector('[name=unit_prices]')?.value)||0)}); $('#liveSubtotal').textContent='$'+t.toFixed(2)}
   $$('.invoice-row').forEach(wire); $('#addLine').onclick=()=>add(); $$('.add-price').forEach(b=>b.onclick=()=>add(b.dataset.id,b.dataset.name,b.dataset.price)); if(!$$('.invoice-row').length)add(); calc();
 }
})();
