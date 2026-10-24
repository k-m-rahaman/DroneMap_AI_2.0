import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source=await readFile(new URL('../src/ui-bridge.js',import.meta.url),'utf8');
const geometry=()=>({type:'Polygon',coordinates:[[[88,22],[88.001,22],[88.001,22.001],[88,22]]]});
function bridge(entry,current){
  const calls=[];
  const context=vm.createContext({state:{entries:new Map([['record',entry]])},geometryOf:()=>current,
    geometryChanged:key=>{calls.push(key);return 'measured';},renderDemoNotice(){},setBusy(){},renderSources(){},
    addEventListener(){},document:{addEventListener(){}},console});
  context.window=context;vm.runInContext(source,context);
  return {changed:()=>context.geometryChanged('record'),calls};
}

test('Geoman close-edit notification does not dirty the saved unchanged boundary',()=>{
  const saved=geometry(),current=structuredClone(saved);current.coordinates[0][0][0]+=1e-12;
  const {changed,calls}=bridge({saved:true,dirty:false,feature:{geometry:saved}},current);
  assert.equal(changed(),undefined);assert.equal(calls.length,0);
});
test('actual boundary vertex moves still reach measurement and dirty tracking',()=>{
  const saved=geometry(),current=structuredClone(saved);current.coordinates[0][1][0]+=0.00001;
  const {changed,calls}=bridge({saved:true,dirty:false,feature:{geometry:saved}},current);
  assert.equal(changed(),'measured');assert.deepEqual(calls,['record']);
});
test('a previously dirty boundary is never suppressed even when coordinates match the saved version',()=>{
  const saved=geometry();
  const {changed,calls}=bridge({saved:true,dirty:true,feature:{geometry:saved}},structuredClone(saved));
  assert.equal(changed(),'measured');assert.deepEqual(calls,['record']);
});
test('unsaved proposals still run initial geometry measurement',()=>{
  const saved=geometry();const {changed,calls}=bridge({saved:false,dirty:false,feature:{geometry:saved}},saved);
  assert.equal(changed(),'measured');assert.deepEqual(calls,['record']);
});
