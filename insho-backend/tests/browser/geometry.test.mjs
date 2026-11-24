import test from "node:test";
import assert from "node:assert/strict";
import {fit, handleRotation, hit, localDelta, nearestGrid, point, previewSnap, screen, visibleGrid, world, zoom}
  from "../../app/web/spatial/geometry.mjs";
import {inverse} from "../../app/web/spatial/store.mjs";

test("viewport maps meters, keeps the cursor anchor, and fits bounds",()=>{
  const camera=fit({min_x_m:0,min_y_m:0,max_x_m:30,max_y_m:10},950,480);
  const cursor=point(200,350), anchor=world(camera,cursor), next=zoom(camera,cursor,camera.scale*2);
  assert.deepEqual(screen(camera,point(15,5)),point(475,240));
  assert.ok(Math.abs(screen(next,anchor).x-cursor.x)<1e-10);
  assert.ok(Math.abs(screen(next,anchor).y-cursor.y)<1e-10);
  assert.ok(screen(camera,point(1,2)).y < screen(camera,point(1,1)).y);
  assert.ok(visibleGrid({x:1,y:2,scale:.001},2000,1000,.01).length<400);
});

test("snap rules match Part 2 half ties, anchor priority, and lexical ties",()=>{
  assert.equal(nearestGrid(-1.5,1),-2);
  const anchors=[{id:"z",kind:"edge_0",x_m:2,y_m:3},{id:"a",kind:"center",x_m:2,y_m:1}];
  assert.deepEqual(previewSnap(point(2,2),.5,anchors,1),{point:point(2,1),kind:"center"});
  assert.deepEqual(previewSnap(point(-1.5,2.5),1,anchors,.01),{point:point(-2,3),kind:"grid"});
});

test("hit testing honors rotated footprints and world movement maps through scaled parents",()=>{
  const diamond={geometry_type:"polygon",bounds:{min_x_m:-2,min_y_m:-2,max_x_m:2,max_y_m:2},
    vertices:[[0,2],[2,0],[0,-2],[-2,0]].map(([x_m,y_m])=>({x_m,y_m}))};
  assert.equal(hit(diamond,point(1.9,1.9),.1),false);
  assert.equal(hit(diamond,point(0,0),.1),true);
  const parent=[[0,-2,0,4],[3,0,0,8],[0,0,1,0],[0,0,0,1]];
  const local=localDelta(parent,point(4,6));
  assert.equal(local.x,2);
  assert.equal(local.y,-2);
  assert.equal(handleRotation(null,point(0,0),point(2,0),point(0,2),15),90);
});

test("delete undo restores group identity and reference edges after endpoints",()=>{
  const snapshot={scene:{id:"scene"},objects:[{id:"a",name:"A"},{id:"b",name:"B",parent_object_id:"a"}],
    assets:[{id:"link",role:"reference",object_id:"b",asset_id:"asset"}],
    relationships:[{id:"edge",source_object_id:"a",target_object_id:"b",relationship_type:"near",metadata:{}}]};
  const undo=inverse(snapshot,[{kind:"delete_object",id:"b"},{kind:"delete_object",id:"a"}]);
  assert.deepEqual(undo.map(op=>op.id),["a","b","link","edge"]);
  assert.equal(undo[1].data.parent_object_id,"a");
  assert.equal(undo[2].object_id,"b");
});
