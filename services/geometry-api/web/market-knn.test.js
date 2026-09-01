const assert = require('assert');
require('./market-knn.js');

const knn = globalThis.DevOSSpatialKNN;
assert(knn, 'DevOSSpatialKNN must be exposed');

const center = knn.geometryCenter({
  type:'Polygon',
  coordinates:[[[101,0],[103,0],[103,2],[101,2],[101,0]]]
});
assert(Math.abs(center.lon-102) < 1e-9);
assert(Math.abs(center.lat-1) < 1e-9);

const projectWithTypes = {
  types:[
    {type:'36/72',category:'komersil',price:250000000,land:72,building:36},
    {type:'45/120',category:'komersil',price:410000000,land:120,building:45},
    {type:'invalid',category:'komersil',price:500000,land:120,building:45},
  ]
};
const rep = knn.selectRepresentativeType(projectWithTypes,'komersil',120);
assert.strictEqual(rep.price,410000000);
assert.strictEqual(rep.landArea,120);

const projects = [
  {name:'A',distance_km:1,types:[{type:'A',category:'komersil',price:300000000,land:120}]},
  {name:'B',distance_km:2,types:[{type:'B',category:'komersil',price:400000000,land:120}]},
  {name:'C',distance_km:3,types:[{type:'C',category:'komersil',price:500000000,land:120}]},
  {name:'D',distance_km:4,types:[{type:'D',category:'subsidi',price:160000000,land:120}]},
];

const result = knn.spatialKnnEstimate(projects,'komersil',120,3);
assert(result);
assert.strictEqual(result.k,3);
assert(result.predictedPrice > 300000000 && result.predictedPrice < 400000000, result.predictedPrice);
assert.strictEqual(result.neighbors[0].project.name,'A');
assert.strictEqual(result.neighbors[2].project.name,'C');
assert(result.conservativePrice >= 300000000 && result.conservativePrice <= 400000000);
assert.strictEqual(result.maxDistanceKm,3);

const allResult = knn.spatialKnnEstimate(projects,'all',120,4);
assert.strictEqual(allResult.k,4);
assert(allResult.predictedPrice < result.predictedPrice, 'nearby subsidy sample should lower all-category estimate');

console.log('Spatial KNN tests passed');
