const assert = require('assert');
require('./market-knn.js');
const knn = globalThis.DevOSSpatialKNN;
assert(knn, 'DevOSSpatialKNN must be exposed');

const center = knn.geometryCenter({type:'Polygon',coordinates:[[[101,0],[103,0],[103,2],[101,2],[101,0]]]});
assert(Math.abs(center.lon-102) < 1e-9);
assert(Math.abs(center.lat-1) < 1e-9);

const projectWithTypes = {types:[
  {type:'36/72',category:'komersil',price:250000000,land:72,building:36},
  {type:'45/120',category:'komersil',price:410000000,land:120,building:45},
  {type:'invalid',category:'komersil',price:500000,land:120,building:45},
]};
const rep = knn.selectRepresentativeType(projectWithTypes,'komersil',120);
assert.strictEqual(rep.price,410000000);
assert.strictEqual(rep.landArea,120);

const projects = [
  {name:'A',distance_km:1,types:[{type:'A',category:'komersil',price:300000000,land:120}]},
  {name:'B',distance_km:2,types:[{type:'B',category:'komersil',price:400000000,land:120}]},
  {name:'C',distance_km:3,types:[{type:'C',category:'komersil',price:500000000,land:120}]},
];
const result = knn.spatialKnnEstimate(projects,'komersil',120,3);
assert(result);
assert.strictEqual(result.k,3);
assert.strictEqual(result.minPrice,300000000);
assert.strictEqual(result.maxPrice,500000000);
assert(result.predictedPrice >= result.minPrice && result.predictedPrice <= result.maxPrice);
assert(result.rawPredictedPrice >= result.minPrice && result.rawPredictedPrice <= result.maxPrice);
assert(result.neighbors.every(n => n.finalWeightShare <= 0.3500001));
assert(Math.abs(result.neighbors.reduce((sum,n)=>sum+n.finalWeightShare,0)-1) < 1e-9);

const outlierProjects = [
  {name:'Near outlier',distance_km:0.05,types:[{type:'X',category:'komersil',price:500000000,land:120}]},
  {name:'B',distance_km:0.6,types:[{type:'B',category:'komersil',price:135000000,land:120}]},
  {name:'C',distance_km:0.7,types:[{type:'C',category:'komersil',price:140000000,land:120}]},
  {name:'D',distance_km:0.8,types:[{type:'D',category:'komersil',price:145000000,land:120}]},
  {name:'E',distance_km:0.9,types:[{type:'E',category:'komersil',price:150000000,land:120}]},
  {name:'F',distance_km:1.0,types:[{type:'F',category:'komersil',price:155000000,land:120}]},
  {name:'G',distance_km:1.1,types:[{type:'G',category:'komersil',price:160000000,land:120}]},
];
const robust = knn.spatialKnnEstimate(outlierProjects,'komersil',120,7);
assert.strictEqual(robust.k,7);
assert.strictEqual(robust.minPrice,135000000);
assert.strictEqual(robust.maxPrice,500000000);
assert(robust.outlierCount >= 1);
assert(robust.predictedPrice < robust.rawPredictedPrice, `${robust.predictedPrice} should be below raw ${robust.rawPredictedPrice}`);
assert(robust.neighbors.every(n => n.finalWeightShare <= 0.3500001));
assert(robust.predictedPrice >= robust.minPrice && robust.predictedPrice <= robust.maxPrice);

console.log('Robust Spatial KNN tests passed');
