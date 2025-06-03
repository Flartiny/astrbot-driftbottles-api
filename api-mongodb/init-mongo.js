// init-mongo.js
db = db.getSiblingDB('DriftBottles');

db.createCollection('Bottles');

db.createRole({
  role: 'bottleManager',
  privileges: [
    {
      resource: { db: 'DriftBottles', collection: 'Bottles' },
      actions: ['find', 'insert', 'update', 'remove']
    }
  ],
  roles: []
});

db.createUser({
  user: 'bottlefinder',
  pwd: 'password', // 可修改为自定义密码
  roles: [
    { role: 'bottleManager', db: 'DriftBottles' }
  ]
});
