// init-mongo.js
db = db.getSiblingDB('DriftBottles');
db.createCollection('Bottles');

db.createCollection('counters'); // 新增：创建计数器集合

db.counters.insertOne({
  _id: 'bottle_id',
  seq: 0
});

db.createRole({
  role: 'bottleManager',
  privileges: [
    {
      resource: { db: 'DriftBottles', collection: 'Bottles' },
      actions: ['find', 'insert', 'update']
    },
    {
      resource: { db: 'DriftBottles', collection: 'counters' },
      actions: ['find', 'update']
    }
  ],
  roles: []
});

db.createUser({
  user: 'bottlefinder',
  pwd: 'password', // 可修改为为自定义密码
  roles: [
    { role: 'bottleManager', db: 'DriftBottles' }
  ]
});