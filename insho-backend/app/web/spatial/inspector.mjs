import {button, choice, el, field, input, number, replace} from "./dom.mjs";
import {round} from "./geometry.mjs";
import {attach, operation} from "./store.mjs";
import {color} from "./viewport.mjs";

const dims = ["width_m","depth_m","height_m"];
const human = {width_m:"Width",depth_m:"Length",height_m:"Height"};
const roleOptions = ["reference","model","preview","material","texture"];
const numeric = (data,key) => {
  const value=data.get(key);
  if (value === null || String(value).trim() === "" || !Number.isFinite(+value)) throw new Error("Enter a number for " + key + ".");
  return +value;
};
const jsonValue = (data,key,array = false) => {
  const value=JSON.parse(data.get(key));
  if (array ? !Array.isArray(value) : !value || typeof value!=="object" || Array.isArray(value)) {
    throw new Error(key + (array ? " must be a JSON array." : " must be a JSON object."));
  }
  return value;
};

export class Inspector {
  constructor(node, editor) {this.node=node;this.editor=editor;}
  render() {
    const e=this.editor, selected=e.selected();
    this.dirty=false;
    this.form=null;
    this.live=el("output",{class:"hint","aria-live":"off"});
    this.issuePanel=el("section",{class:"issues stack"});
    if (selected.length===1) {
      const row=selected[0], zone=e.isZone(row), g=e.data.geometry[row.id], kind=zone?"zone":"object";
      const descendants=e.descendants([row.id]);
      const parents=(zone?e.data.zones:e.data.objects).filter(r=>!descendants.has(r.id));
      const factor=e.factor;
      const lengthInput=(key,value,attrs={})=>number(key,value==null?"":+(Number(value)/factor).toPrecision(13),attrs);
      const prefix=row.parent_object_id||row.parent_zone_id?"Parent-local":"Scene";
      const form=this.form=el("form",{class:"stack",on:{
        input:()=>{if(!this.dirty){this.dirty=true;e.renderBars();}},
        submit:event=>{event.preventDefault();if(e.editable)this.apply(row,kind);},
      }},el("fieldset",{disabled:!e.data.can_edit},el("legend",{},zone?"Zone":"Object"),
        el("div",{class:"stack"},
          field("Name",input("name",row.name,{required:true,maxlength:zone?160:200})),
          field("Category",input("category",row.category,{required:true,maxlength:zone?60:80})),
          field("Visibility",choice("status",[["active","Visible"],["hidden","Hidden"],["archived","Archived"]],row.status)),
          field("Parent",choice("parent",[["","None"],...parents.map(p=>[p.id,p.name])],row.parent_object_id||row.parent_zone_id)),
          zone ? null : field("Assigned zone",choice("zone",[["","None"],...e.data.zones.map(z=>[z.id,z.name])],row.zone_id)),
          field("Color",input("color",color(row,zone),{type:"color"})))),
        el("fieldset",{disabled:!e.data.can_edit},el("legend",{},prefix+" position ("+e.unit+")"),
          el("div",{class:"fields"},["x","y","z"].map(k=>field(k.toUpperCase(),lengthInput(k,row.transform.position[k],{required:true}))))),
        el("fieldset",{disabled:!e.data.can_edit},el("legend",{},"Local dimensions ("+e.unit+")"),
          el("div",{class:"fields"},dims.map(k=>field(human[k],lengthInput(k,row.dimensions?.[k],
            {required:!zone,min:.000001/factor,max:99999999/factor}))))),
        el("fieldset",{disabled:!e.data.can_edit},el("legend",{},"Rotation (°)"),
          el("div",{class:"fields"},["x","y","z"].map(k=>field(k.toUpperCase(),number("angle_"+k,row.transform.rotation_deg[k],{required:true}))))),
        el("details",{},el("summary",{},"Advanced"),el("fieldset",{disabled:!e.data.can_edit},el("legend",{},"Canonical properties"),
          zone ? field("Description",input("description",row.description||"")) : null,
          zone ? null : field("Object type",input("object_type",row.object_type,{required:true,maxlength:80})),
          field(zone?"Metadata (JSON)":"Custom properties (JSON)",el("textarea",{name:"properties",rows:4},JSON.stringify(zone?row.metadata:row.properties,null,2))),
          zone ? null : field("Material references (JSON)",el("textarea",{name:"materials",rows:3},JSON.stringify(row.material_refs,null,2))))),
        e.data.can_edit ? el("div",{class:"row"},el("button",{type:"submit",class:"primary"},"Apply properties"),
          button("Cancel edits",()=>{this.render();e.renderBars();})) : null);
      const measurements=g?.bounds ? el("p",{class:"hint"},"Projected bounds "+e.format(g.bounds.width_m)+" × "+e.format(g.bounds.depth_m)) : null;
      replace(this.node,el("h2",{},"Inspector"),el("div",{class:"stack"},form,this.live,measurements,
        g?.error ? el("p",{class:"error"},g.error) : null,zone?null:this.assetPanel(row)),this.issuePanel);
    } else if (selected.length>1) {
      replace(this.node,el("h2",{},"Inspector"),el("p",{},selected.length+" selected"),this.live,
        selected.length===2 && !selected.some(row=>e.isZone(row))?this.clearance(selected):null,this.issuePanel);
    } else {
      replace(this.node,el("h2",{},"Scene"),el("div",{class:"stack"},
        el("p",{},e.data.scene.name),
        el("p",{class:"hint"},dims.map(k=>e.format(e.data.scene.dimensions[k])).join(" × ")),
        el("p",{class:"hint"},e.data.can_edit?"Select a shape or layer to edit its properties.":"You have view access to this scene."),
        e.data.can_edit?this.sceneSettings():null),this.issuePanel);
    }
    this.renderIssues();
  }
  lock(busy) {
    if (!this.form || !this.editor.data.can_edit) return;
    for(const node of this.form.querySelectorAll("fieldset, button")) node.disabled=busy;
  }
  apply(row,kind) {
    const e=this.editor,data=new FormData(this.form),zone=kind==="zone";
    try {
      const name=data.get("name").trim(), category=data.get("category").trim();
      if(!name||!category)throw new Error("Name and category are required.");
      let dimensions=null;
      if (!zone || dims.some(k=>data.get(k)!=="")) {
        dimensions=Object.fromEntries(dims.map(k=>[k,round(numeric(data,k)*e.factor)]));
        if (Object.values(dimensions).some(v=>v<.000001||v>=100000000)) throw new Error("Dimensions must be positive and within the stored meter range.");
      }
      const transform={...row.transform,
        position:Object.fromEntries(["x","y","z"].map(k=>[k,round(numeric(data,k)*e.factor)])),
        rotation_deg:Object.fromEntries(["x","y","z"].map(k=>[k,round(numeric(data,"angle_"+k))]))};
      const appearance={...row.appearance};
      if (data.get("color")!==color(row,zone)) appearance.color=data.get("color");
      const payload={name,category,dimensions,transform,appearance,status:data.get("status")};
      const properties=jsonValue(data,"properties");
      if (zone) {
        Object.assign(payload,{parent_zone_id:data.get("parent")||null,description:data.get("description")||null,metadata:properties});
        if(row.footprint && row.dimensions && dimensions) {
          payload.footprint=row.footprint.map(p=>({x_m:round(+p.x_m*dimensions.width_m/+row.dimensions.width_m),
            y_m:round(+p.y_m*dimensions.depth_m/+row.dimensions.depth_m)}));
        }
      } else {
        const object_type=data.get("object_type").trim();
        if(!object_type)throw new Error("Object type is required.");
        Object.assign(payload,{parent_object_id:data.get("parent")||null,zone_id:data.get("zone")||null,object_type,
          properties,material_refs:jsonValue(data,"materials",true)});
      }
      this.dirty=false;
      e.commit([operation("update_"+kind,row.id,payload)]);
    } catch(error) {e.message(error.message);}
  }
  preview(position,dimensions,angle) {
    if(!this.live)return;
    const e=this.editor, parts=[];
    if(position)parts.push("X "+e.format(position.x)+" · Y "+e.format(position.y)+" · Z "+e.format(position.z));
    if(dimensions)parts.push(e.format(dimensions.width_m)+" × "+e.format(dimensions.depth_m));
    if(angle!==undefined)parts.push(angle+"°");
    this.live.textContent=parts.join(" · ");
  }
  assetPanel(row) {
    const e=this.editor, assets=e.data.assets.filter(a=>a.object_id===row.id), list=el("ul",{},
      assets.map(asset=>el("li",{class:"row"},el("span",{},asset.role+" · "+asset.asset_metadata.category),
        button("Detach",()=>e.commit([operation("detach_asset",asset.id)]),{disabled:!e.data.can_edit}))));
    const box=el("details",{},el("summary",{},"Asset references ("+assets.length+")"),list);
    if(!e.data.can_edit)return box;
    const results=el("div",{class:"stack"});
    box.append(button("Browse project assets",async()=>{
      results.replaceChildren("Loading assets…");
      try {
        const p=e.project, q=new URLSearchParams({organization_id:p.organization_id,limit:100});
        const available=(await e.request("/assets?"+q)).filter(a=>(!a.project_id||a.project_id===p.id)&&
          (!a.workspace_id||a.workspace_id===p.workspace_id)&&(!a.event_id||a.event_id===e.data.scene.event_id));
        if(!available.length){results.replaceChildren("No compatible assets found.");return;}
        const select=choice("asset",available.map(a=>[a.id,a.display_name]),available[0].id,{"aria-label":"Asset"});
        const role=choice("role",roleOptions.map(x=>[x,x]),"reference",{"aria-label":"Asset role"});
        results.replaceChildren(select,role,button("Attach",()=>e.commit([attach({asset_id:select.value,role:role.value},
          crypto.randomUUID(),row.id)])));
      } catch(error) {results.replaceChildren(el("p",{class:"error"},error.message));}
    }),results);
    return box;
  }
  sceneSettings() {
    const e=this.editor, clearance=number("clearance",e.settings.clearance_m/e.factor,{min:0,required:true});
    return el("form",{class:"stack",on:{submit:event=>{
      event.preventDefault();const value=+clearance.value*e.factor;
      if(Number.isFinite(value))e.setting({clearance_m:round(value)});
    }}},field("Clearance target ("+e.unit+")",clearance),
    el("button",{type:"submit"},"Set clearance target"));
  }
  clearance(rows) {
    const e=this.editor, output=el("span",{role:"status"});
    const btn=button("Measure clearance",async()=>{
      btn.disabled=true;output.textContent="Measuring…";
      try {
        const measurement=await e.request(e.path+"/clearance","POST",{first_object_id:rows[0].id,
          second_object_id:rows[1].id,requested_clearance_m:e.settings.clearance_m});
        output.className=measurement.clearance_met?"hint":"error";
        output.textContent=e.format(measurement.distance_m)+(measurement.clearance_met?"":" · Below "+e.format(e.settings.clearance_m)+" target");
      } catch(error) {output.className="error";output.textContent=error.message;}
      finally {btn.disabled=false;}
    });
    return el("div",{class:"stack"},btn,output);
  }
  renderIssues() {
    if(!this.issuePanel)return;
    const e=this.editor,issues=e.issues();
    replace(this.issuePanel,el("h2",{},"Geometry checks"),
      e.validation?.error?el("p",{class:"error"},e.validation.error):
      !e.validation?el("p",{class:"hint"},"Checking geometry…"):
      !issues.length?el("p",{class:"hint"},"No geometry findings."):
      el("ul",{},issues.slice(0,150).map(issue=>el("li",{},button(
        (e.rows.get(issue.id)?.name||"Geometry")+ (issue.other?" / "+e.rows.get(issue.other)?.name:"")+" · "+issue.text,()=>{
          if(e.select(issue.id))e.viewport.fit("selection");
        })))),
      issues.length>150?el("p",{class:"hint"},(issues.length-150)+" additional findings."):null,
      button("Check again",()=>e.validate()));
  }
}
